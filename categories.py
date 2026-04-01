"""
Category classification for pantry items.
Hardcoded keyword table — user overrides are stored in the DB and take precedence.
"""
import re

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Produce":   ["apple","banana","orange","grape","berry","berries","lettuce","spinach","kale",
                  "tomato","tomatoes","carrot","carrots","onion","onions","garlic","potato","potatoes",
                  "broccoli","cucumber","pepper","peppers","celery","mushroom","mushrooms","zucchini",
                  "avocado","lemon","lime","mango","melon","peach","pear","plum","strawberry",
                  "blueberry","raspberry","corn","peas","beans","herb","herbs","ginger","fruit","vegetable"],
    "Dairy":     ["milk","cheese","butter","yogurt","yoghurt","cream","egg","eggs","sour cream",
                  "cottage cheese","mozzarella","cheddar","parmesan","brie","feta","half and half",
                  "whipping cream","heavy cream","oat milk","almond milk","soy milk"],
    "Meat":      ["chicken","beef","pork","lamb","turkey","bacon","sausage","ham","steak","ground",
                  "fish","salmon","tuna","shrimp","seafood","lobster","crab","tilapia","cod","meat",
                  "deli","salami","pepperoni","prosciutto","veal","brisket","ribs","wings","drumstick"],
    "Bakery":    ["bread","bagel","muffin","croissant","roll","bun","cake","pie","cookie","cookies",
                  "donut","pastry","tortilla","pita","naan","sourdough","baguette","loaf","flour","yeast"],
    "Drinks":    ["water","juice","soda","pop","coffee","tea","beer","wine","spirits","vodka","whiskey",
                  "rum","gin","tequila","lemonade","kombucha","energy drink","sports drink","milk shake",
                  "smoothie","cider","sparkling","coconut water","drink","beverage"],
    "Frozen":    ["frozen","ice cream","gelato","sorbet","pizza","nugget","waffle","fries","edamame",
                  "ice","popsicle","frozen meal","frozen dinner","frozen vegetable","frozen fruit"],
    "Pantry":    ["rice","pasta","noodle","quinoa","oat","oatmeal","cereal","granola","soup","broth",
                  "stock","can","canned","sauce","salsa","ketchup","mustard","mayo","mayonnaise",
                  "oil","olive oil","vinegar","honey","jam","jelly","peanut butter","almond butter",
                  "nut butter","syrup","salt","pepper","spice","spices","seasoning","sugar","baking",
                  "chocolate","cocoa","vanilla","lentil","chickpea","bean","beans","coconut milk"],
    "Snacks":    ["chip","chips","cracker","crackers","popcorn","pretzel","nut","nuts","almond","cashew",
                  "walnut","peanut","trail mix","granola bar","protein bar","candy","chocolate bar",
                  "gummy","snack","jerky","dried fruit","raisin"],
    "Household": ["soap","shampoo","conditioner","detergent","cleaner","bleach","sponge","paper towel",
                  "toilet paper","tissue","trash bag","garbage bag","foil","wrap","plastic wrap",
                  "zip bag","laundry","dish","dishwasher","toothpaste","toothbrush","deodorant",
                  "razor","lotion","sunscreen","vitamin","medicine","bandage","cleaning"],
    "Other":     [],  # catch-all — always last
}

VALID_CATEGORIES = set(CATEGORY_KEYWORDS.keys())


def classify_item(name: str) -> str:
    """Auto-classify an item name into a category by keyword matching."""
    lower = name.lower()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if category == "Other":
            continue
        if any(kw in lower for kw in keywords):
            return category
    return "Other"


def try_add_quantities(existing: str, incoming: str) -> str:
    """
    Numerically add two quantity strings where possible.
    Examples:
      "2"    + "3"    → "5"
      "500g" + "200g" → "700g"
      "1L"   + "500ml"→ "500ml"   (unit mismatch → keep incoming)
      "a few"+ "2"    → "2"       (non-numeric → keep incoming)
    """
    def parse(q: str):
        m = re.match(r'^\s*(\d+\.?\d*)\s*([a-zA-Z]*)\s*$', q.strip())
        if m:
            return float(m.group(1)), m.group(2).lower()
        return None, None

    ev, eu = parse(existing)
    nv, nu = parse(incoming)

    if ev is not None and nv is not None and eu == nu:
        total = ev + nv
        total_str = str(int(total)) if total == int(total) else str(round(total, 4))
        return f"{total_str}{eu}" if eu else total_str

    # Units mismatch or non-numeric — keep the incoming value
    return incoming


def classify_item_with_overrides(name: str, get_db_fn, q_fn, fetchone_fn) -> str:
    """
    Classify an item, checking user overrides in DB before keyword matching.
    Pass in get_db, q, fetchone from database module to avoid circular imports.
    """
    try:
        conn = get_db_fn()
        cur  = conn.cursor()
        cur.execute(q_fn("SELECT category FROM user_category_overrides WHERE name_lower = ?"),
                    (name.strip().lower(),))
        row = fetchone_fn(cur)
        cur.close(); conn.close()
        if row:
            return row["category"]
    except Exception:
        pass
    return classify_item(name)


def save_category_override(name: str, category: str, get_db_fn, q_fn) -> None:
    """Persist a user category override so future auto-classifications use it."""
    try:
        conn = get_db_fn()
        cur  = conn.cursor()
        # Upsert: update if exists, insert if not
        try:
            cur.execute(
                q_fn("INSERT INTO user_category_overrides (name_lower, category) VALUES (?, ?)"),
                (name.strip().lower(), category)
            )
        except Exception:
            conn.rollback()
            cur.execute(
                q_fn("UPDATE user_category_overrides SET category = ? WHERE name_lower = ?"),
                (category, name.strip().lower())
            )
        conn.commit()
        cur.close(); conn.close()
    except Exception:
        pass
