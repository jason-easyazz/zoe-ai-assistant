# Grocery & Meal Planning Skill

<!-- metadata.when: user asks about shopping list, groceries, meal planning, what to cook, or recipe suggestions -->


Smart grocery list management and meal planning for the family.

## Tools

### Add an item to the shopping list
```
mcporter-safe call zoe-data.list_add_item list_type=shopping text="Milk" quantity="2L" category="dairy"
```

### Add to a named list (e.g. Bunnings, Woolworths run)
```
mcporter-safe call zoe-data.list_add_item list_type=shopping list_name="Bunnings" text="Sandpaper" category="hardware"
```

### View the shopping list
```
mcporter-safe call zoe-data.list_get_items list_type=shopping
```

### View a named list
```
mcporter-safe call zoe-data.list_get_items list_type=shopping list_name="Bunnings"
```

### Check off / remove an item
```
mcporter-safe call zoe-data.list_remove_item list_type=shopping item_text="Milk"
```

## Smart Categories

When adding items, auto-categorize in the `category` parameter:
- **Produce:** fruits, vegetables, herbs → `produce`
- **Dairy:** milk, cheese, yogurt, eggs, butter → `dairy`
- **Meat:** chicken, beef, pork, fish, seafood → `meat`
- **Bakery:** bread, rolls, pastries → `bakery`
- **Pantry:** pasta, rice, canned goods, oils, spices → `pantry`
- **Frozen:** ice cream, frozen meals, frozen vegetables → `frozen`
- **Drinks:** juice, soda, water, coffee, tea → `drinks`
- **Household:** cleaning supplies, paper products → `household`
- **Personal:** toiletries, health items → `personal`

## Quantity Parsing

Parse natural quantities into the `quantity` parameter:
- "a dozen eggs" → `quantity="12"`, `text="Eggs"`
- "2 litres of milk" → `quantity="2L"`, `text="Milk"`
- "500g mince" → `quantity="500g"`, `text="Beef Mince"`

## Recipe to List

When someone shares a recipe or says "we're making X tonight":
1. Look up ingredients for that dish (using web_search or your training knowledge)
2. Ask "Want me to add the ingredients to the shopping list?"
3. Check what might already be on the list to avoid duplicates: `list_get_items list_type=shopping`
4. Add each missing ingredient as a separate `list_add_item` call

## Meal Planning

When asked about meal planning:
- Check what's on the shopping list first with `list_get_items`
- Suggest meals based on available or nearly-available ingredients
- Consider dietary preferences from family profiles (USER.md)
- Rotate suggestions to avoid repetition
