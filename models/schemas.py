"""Pydantic schemas -- validation at the boundary, before anything reaches the DB.

`FoodItem` and `ExtractedMeal` are the LLM's structured-output contract. Note what
they do *not* contain: no kcal, no macro fields. The model reports what was eaten
and how much; every number comes from the nutrition lookup afterwards.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from calc.tdee import ActivityLevel, GoalType, Sex


class ProfileInput(BaseModel):
    """Body stats collected during onboarding."""

    model_config = ConfigDict(use_enum_values=False)

    height_cm: float = Field(gt=0, le=280)
    weight_kg: float = Field(gt=0, le=400)
    age: int = Field(gt=0, le=120)
    sex: Sex
    activity_level: ActivityLevel
    goal_type: GoalType


class MealItem(BaseModel):
    """A single food within a meal, with macros already resolved.

    Macro values are never model-generated -- Phase 1 passes them in by hand, and
    from Phase 2 they come from the nutrition lookup.
    """

    food_name: str = Field(min_length=1)
    quantity: float = Field(gt=0)
    unit: str = Field(min_length=1)
    kcal: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    carbs_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)


class FoodItem(BaseModel):
    """One food the user mentioned, as extracted by the LLM.

    Deliberately carries no macro fields -- the model's job ends at "what and how
    much". Adding a kcal field here would invite exactly the hallucinated numbers
    this design exists to prevent.
    """

    name: str = Field(description="The food, as plainly as possible, e.g. 'roti', 'dal'")
    quantity: float = Field(gt=0, description="How many, e.g. 2 for '2 rotis'")
    unit: str = Field(
        description="Unit as the user said it: piece, bowl, katori, plate, glass, g, ml"
    )


class ExtractedMeal(BaseModel):
    """Everything the LLM pulled out of one 'I ate ...' message."""

    items: list[FoodItem] = Field(description="Every distinct food mentioned")


class ExtractedProfile(BaseModel):
    """Profile fields found in an onboarding message. All optional -- missing
    fields are asked for in follow-up turns."""

    height_cm: float | None = Field(default=None, description="Height in centimetres")
    weight_kg: float | None = Field(default=None, description="Weight in kilograms")
    age: int | None = Field(default=None, description="Age in years")
    sex: Sex | None = Field(default=None, description="male or female")
    activity_level: ActivityLevel | None = Field(
        default=None,
        description="sedentary, light, moderate, active, or very_active",
    )
    goal_type: GoalType | None = Field(
        default=None, description="cut, maintain, or bulk"
    )


class MealInput(BaseModel):
    """A meal awaiting confirmation or save."""

    raw_text: str = Field(min_length=1)
    items: list[MealItem] = Field(min_length=1)

    @property
    def total_kcal(self) -> float:
        return sum(item.kcal for item in self.items)

    @property
    def total_protein_g(self) -> float:
        return sum(item.protein_g for item in self.items)
