# Children's Toys — Image Classification SOP (marketplace compliance)

> **Provenance.** This is a rewritten, public edition of a real classification SOP. The
> reasoning is preserved; four things changed. The program name was generic-ised. A pair of
> internal two-letter verdict abbreviations — which denoted *in scope* and *out of scope*,
> and notably did NOT carry their usual statistical meaning — are now spelled out as
> `IN_SCOPE` and `OUT_OF_SCOPE`, because the originals were both internal jargon and
> actively misleading. One catalog attribute name of uncertain public status was dropped.
> An internal case-directory convention was removed.
>
> **This SOP is deliberately shipped with its defects intact.** `sop_screen.lint` finds
> them, and the findings are the linter's regression fixtures. Do not "tidy" the
> contradictions below — several tests assert they are still here.

## 1. Rule scope

Covers **toys** — objects designed for play and entertainment, primarily intended for
children. A product is in scope when it is marketed for or intended for use by a child:

| Region | Store | Age range |
|---|---|---|
| NA | US | child 12 years or younger |
| NA | CA | child 13 years or younger |
| IN | IN | child 3 years or younger (36 months). Any toy for 3 years+ is out of scope; classify by age grading only |
| MEA | AE/SA/EG/ZA | child 13 years or younger |

A toy is "children's" when it is represented in packaging or advertising as appropriate
for a child, has play value for a child, and/or is designed and themed for children
rather than adult collectors.

## 2. In-scope product types

1. Action figures, dolls and collectibles (incl. dollhouses and accessories)
2. Outdoor and active play toys
3. Vehicle and remote-control toys
4. Pretend and role-play toys
5. Infant, toddler and soft/plush toys
6. Games and education toys (incl. puzzles)
7. Sensory and creative play (incl. squishy toys, slime, kinetic sand)

## 3. Visual classification signals

### 3.1 Explicit children's marketing (IN_SCOPE indicators)

**Licensed characters / child-appealing IP (strong IN_SCOPE if present):**
Pokemon, Barbie, Hot Wheels, Paw Patrol, Peppa Pig, Disney Princesses, Marvel /
Spider-Man, Star Wars (play versions), Sonic, LOL Surprise!, Baby Shark, Bluey,
Cocomelon, Squishmallows, Beanie Babies, Thomas & Friends, LEGO / building sets,
Fortnite, Minecraft, Nintendo characters (play toys), Dragon Ball, cartoonish mascots.

- **Pokemon cards -> IN_SCOPE regardless of claims** (child-appealing).
- Anime alone is general-use (NOT by itself a children's indicator).

**Children's text claims (IN_SCOPE if visible on product or packaging):**
kid, kids, child, children, boy, girl, toddler, baby, youth, "for ages",
"pretend play", "role play", play set, playset, "fun for kids".

**Age indicators:**
- Any stated age 12 and below (US) = in scope.
- "All ages" / "for all ages" / "kids of all ages" = in scope (includes children).
- "14+" / "suitable for above 14" / "adult collectors only" = out of scope
  (but does NOT override an explicit IN_SCOPE children's claim elsewhere).

### 3.1.1 Age interpretation rules (by source field)

| Source field | Interpretation |
|---|---|
| Title, bullets, product/item description | number with no unit (e.g. "3+") -> **years** |
| Package age visible on image | number with no unit (e.g. "3+") -> **years** |
| manufacturer_minimum_age / minimum_age | always **months** |
| age_range_description | explicit unit if given; bare number (e.g. "18+", "6") -> **months** |

Examples: `age_range_description="18+"` -> 18 months (~1.5y) = child = IN_SCOPE;
package "Ages 3+" -> 3 years = child = IN_SCOPE; `manufacturer_minimum_age="36"` ->
36 months (3y) = child = IN_SCOPE.

> **IN store only:** classify by age grading alone. A toy with no age range specified is
> OUT_OF_SCOPE regardless of claims. 3+ years = OUT_OF_SCOPE; 0-3y / 0-36mo / 0-5y /
> 2-7y / 0-14y = IN_SCOPE.

### 3.2 Children's visual indicators (IN_SCOPE signals)

- Bright primary colours (pink, blue, yellow, green), cartoonish styling.
- Soft plastic, rubber, plush or fabric construction.
- Character-themed or mascot designs.
- Child-sized proportions; large chunky parts for small hands.
- Play value: movable/poseable/articulated joints, interchangeable or
  buildable/snap-together parts, mini/collectible play parts, sound or light play.
- Packaging shows a child using, holding or playing with the product.
- Party/birthday/playroom/nursery backdrop; toy-like bright packaging.

### 3.3 Adult / collector use indicators (OUT_OF_SCOPE signals)

- Materials signalling collector or decor use: metal, wood, porcelain, glass, resin,
  high-quality or premium finish.
- Display-only cues: fixed base or pedestal, no interchangeable parts, "for display",
  "collector's edition", packaging intended to be displayed.
- Target audience stated as adult collectors; sophisticated or minimalist design.
- Themes inappropriate for children: dark, gothic, sexual, religious.
- High price relative to equivalent toys (NOT a standalone criterion).

### 3.4 Intent over claims

Classify by **intent and play value**, not by age claims alone. A tiny figurine in bright
colours advertised as a toy is IN_SCOPE even without an age claim; a scale model with a
fixed base sold for display is OUT_OF_SCOPE even if a child could theoretically hold it.

## 4. Out-of-scope products (approve regardless of children's appeal)

- Display-only scale models (aeroplane, car) with no play claim and a fixed base.
- Display boxes, cases, stands or accessories that only hold collectibles.
- Books, music, videos, DVDs (unless bundled with a toy).
- Posters, prints, stickers, decals.
- Toys marketed for pets.
- Children's arts and crafts supplies (standalone).
- Cake toppers with no toy claim and no child shown using them.
- Games consoles as general-use devices; smart or fitness devices.
- Sports collectible cards and Magic: The Gathering (general-use / complex).
- Home decorative items; bobbleheads intended only for display.
- Products with sexually explicit themes.

## 5. Edge case decision framework

1. Child shown in image interacting with the product? -> likely IN_SCOPE.
2. Themed with a child-appealing character from the list? -> IN_SCOPE.
3. Visible text references kids/child/boy/girl/toddler/play/ages <=12? -> IN_SCOPE.
4. Soft plastic or plush AND brightly coloured with play value? -> likely IN_SCOPE.
5. Made of metal/wood/resin with a display base and "collector" framing? -> OUT_OF_SCOPE.
6. Description references only adult or display use, no play value? -> OUT_OF_SCOPE signal.

**Key rule:** A single genuine IN_SCOPE children's or play claim takes precedence over
adult or collector claims. Presence of an in-scope age *alone* is not sufficient for
collectibles when all other signals point to adult or display use — weigh play value and
intent.
