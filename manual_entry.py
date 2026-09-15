"""Phase 1 CLI harness: prove the DB + TDEE math work end to end without an LLM.

Macro numbers are typed in by hand here. Phase 2 replaces that with the nutrition
lookup; this file is scaffolding, not part of the final app.

    python manual_entry.py profile --height 178 --weight 75 --age 28 \\
        --sex male --activity moderate --goal cut
    python manual_entry.py log --text "2 rotis and a bowl of dal" \\
        --item "roti:2:piece:240:6.4:48:1.6" \\
        --item "dal:1:bowl:180:12.0:24:3.0"
    python manual_entry.py today
"""

from __future__ import annotations

import argparse
import sys

from calc.tdee import KCAL_DELTA, ActivityLevel, GoalType, Sex, compute_targets
from models.db import (
    get_daily_tally,
    get_meals_for_day,
    get_profile,
    init_db,
    save_meal,
    save_profile,
    session_scope,
)
from models.schemas import MealInput, MealItem, ProfileInput

ITEM_FIELDS = "food:qty:unit:kcal:protein_g:carbs_g:fat_g"


def parse_item(raw: str) -> MealItem:
    """Parse a colon-delimited `--item` string into a validated MealItem."""
    parts = raw.split(":")
    if len(parts) != 7:
        raise argparse.ArgumentTypeError(
            f"expected 7 colon-separated fields ({ITEM_FIELDS}), got {len(parts)}: {raw!r}"
        )
    food_name, quantity, unit, kcal, protein_g, carbs_g, fat_g = parts
    return MealItem(
        food_name=food_name.strip(),
        quantity=float(quantity),
        unit=unit.strip(),
        kcal=float(kcal),
        protein_g=float(protein_g),
        carbs_g=float(carbs_g),
        fat_g=float(fat_g),
    )


def cmd_profile(args: argparse.Namespace) -> None:
    """Compute targets from body stats and store the single profile row."""
    profile_input = ProfileInput(
        height_cm=args.height,
        weight_kg=args.weight,
        age=args.age,
        sex=Sex(args.sex),
        activity_level=ActivityLevel(args.activity),
        goal_type=GoalType(args.goal),
    )
    targets = compute_targets(
        weight_kg=profile_input.weight_kg,
        height_cm=profile_input.height_cm,
        age=profile_input.age,
        sex=profile_input.sex,
        activity_level=profile_input.activity_level,
        goal_type=profile_input.goal_type,
    )

    with session_scope() as session:
        save_profile(
            session,
            height_cm=profile_input.height_cm,
            weight_kg=profile_input.weight_kg,
            age=profile_input.age,
            sex=profile_input.sex.value,
            activity_level=profile_input.activity_level.value,
            goal_type=profile_input.goal_type.value,
            target_kcal=targets.target_kcal,
            target_protein_min_g=targets.target_protein_min_g,
            target_protein_max_g=targets.target_protein_max_g,
        )

    print(f"BMR              {targets.bmr:.1f} kcal")
    print(f"TDEE             {targets.tdee:.1f} kcal  ({profile_input.activity_level.value})")
    print(f"Goal             {profile_input.goal_type.value}")
    print(f"Target kcal      {targets.target_kcal}")
    if targets.kcal_clamped_to_bmr:
        print(
            f"                 clamped up to BMR -- a {abs(KCAL_DELTA[profile_input.goal_type])}"
            f" kcal deficit would have put you below resting expenditure"
        )
    print(
        f"Target protein   {targets.target_protein_min_g}-{targets.target_protein_max_g} g"
        f"  (mid {targets.target_protein_mid_g} g)"
    )


def cmd_log(args: argparse.Namespace) -> None:
    """Save a hand-entered meal, then print the updated daily tally."""
    meal = MealInput(raw_text=args.text, items=args.item)

    with session_scope() as session:
        save_meal(
            session,
            raw_text=meal.raw_text,
            items=[item.model_dump() for item in meal.items],
        )

    print(f"Logged: {meal.raw_text}")
    for item in meal.items:
        print(
            f"  {item.food_name} x{item.quantity:g} {item.unit}"
            f"  {item.kcal:.0f} kcal / {item.protein_g:.1f} g protein"
        )
    print(f"  meal total: {meal.total_kcal:.0f} kcal / {meal.total_protein_g:.1f} g protein")
    print()
    print_today()


def print_today() -> None:
    """Print today's meals and the running tally against the stored targets."""
    with session_scope() as session:
        profile = get_profile(session)
        kcal, protein = get_daily_tally(session)
        meals = get_meals_for_day(session)

        print(f"Today: {len(meals)} meal(s) logged")
        for meal in meals:
            print(
                f"  [{meal.timestamp:%H:%M}] {meal.raw_text}"
                f"  -> {meal.total_kcal:.0f} kcal / {meal.total_protein_g:.1f} g"
            )

        if profile is None:
            print(f"\nTotal: {kcal:.0f} kcal / {protein:.1f} g protein")
            print("No profile set -- run the `profile` command to get targets.")
            return

        print(
            f"\nkcal     {kcal:.0f} / {profile.target_kcal}"
            f"   ({profile.target_kcal - kcal:+.0f} remaining)"
        )
        band = f"{profile.target_protein_min_g}-{profile.target_protein_max_g} g"
        if protein < profile.target_protein_min_g:
            note = f"{profile.target_protein_min_g - protein:+.1f} to reach the band"
        elif protein <= profile.target_protein_max_g:
            note = "in band"
        else:
            note = f"{protein - profile.target_protein_max_g:.1f} over the band"
        print(f"protein  {protein:.1f} / {band}   ({note})")


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse CLI for the three Phase 1 commands."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    profile = subparsers.add_parser("profile", help="set body stats and compute targets")
    profile.add_argument("--height", type=float, required=True, help="height in cm")
    profile.add_argument("--weight", type=float, required=True, help="weight in kg")
    profile.add_argument("--age", type=int, required=True)
    profile.add_argument("--sex", choices=[s.value for s in Sex], required=True)
    profile.add_argument(
        "--activity", choices=[a.value for a in ActivityLevel], required=True
    )
    profile.add_argument("--goal", choices=[g.value for g in GoalType], required=True)
    profile.set_defaults(func=cmd_profile)

    log = subparsers.add_parser("log", help="log a meal with hand-entered macros")
    log.add_argument("--text", required=True, help="what you'd have typed in chat")
    log.add_argument(
        "--item",
        type=parse_item,
        action="append",
        required=True,
        metavar=ITEM_FIELDS,
        help="repeatable; one per food",
    )
    log.set_defaults(func=cmd_log)

    today = subparsers.add_parser("today", help="show today's tally against your goal")
    today.set_defaults(func=lambda args: print_today())

    return parser


def main(argv: list[str] | None = None) -> int:
    init_db()
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
