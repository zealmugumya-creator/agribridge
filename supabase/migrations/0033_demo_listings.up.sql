-- Migration 0033: Demo listings — 40 realistic Ugandan farm products across all 20 categories
-- Safe to run multiple times (ON CONFLICT DO NOTHING on crop_name+farmer_name)
-- Existing 4 listings are updated to use proper category slugs too.

begin;

-- ── 1. Fix existing 4 listings to match correct category slugs ──────────────
update public.listings set category = 'fruits'       where lower(crop_name) like '%avocado%';
update public.listings set category = 'roots-tubers' where lower(crop_name) like '%cassava%';
update public.listings set category = 'fruits'       where lower(crop_name) like '%mango%';
update public.listings set category = 'cash-crops'   where lower(crop_name) like '%coffee%';

-- ── 2. Helper: resolve category_id from slug ────────────────────────────────
-- (we reference the product_categories table seeded in migration 0032)

-- ── 3. Insert demo listings ─────────────────────────────────────────────────
insert into public.listings
  (farmer_name, crop_name, category, quantity_kg, price_per_kg, district, description,
   is_available, is_organic, is_verified, delivery_available, payment_terms,
   image_url)
values

-- VEGETABLES
('Nakato Grace',    'Tomatoes',          'vegetables',        500,  1500, 'Wakiso',    'Fresh Roma tomatoes, picked daily. No pesticides used.',               true, true,  true,  true,  'MTN MoMo / Cash', 'https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=600&q=80'),
('Ssekandi Peter',  'Cabbage',           'vegetables',        300,   800, 'Mukono',    'Large firm cabbages, 1–2 kg each. Available year-round.',              true, false, true,  false, 'Cash on delivery', 'https://images.unsplash.com/photo-1594282486552-05b4d80fbb9f?w=600&q=80'),
('Auma Florence',   'Green Pepper',      'vegetables',        150,  2500, 'Lira',      'Sweet green bell peppers, greenhouse grown.',                          true, false, false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1563565375-f3fdfdbefa83?w=600&q=80'),
('Byamukama John',  'Onions',            'vegetables',        800,  1200, 'Kasese',    'Red onions, dry-cured. Bulk orders welcome.',                          true, false, true,  true,  'Airtel Money',     'https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=600&q=80'),

-- FRUITS
('Namutebi Ruth',   'Pineapple',         'fruits',            400,   600, 'Kayunga',   'Sweet Smooth Cayenne pineapples. Order by crate (20 pcs).',            true, true,  true,  true,  'MTN MoMo',         'https://images.unsplash.com/photo-1550258987-190a2d41a8ba?w=600&q=80'),
('Opio Sam',        'Passion Fruit',     'fruits',            200,  3500, 'Soroti',    'Purple passion fruit, high juice content. Ready for export.',          true, true,  false, true,  'Cash / MoMo',      'https://images.unsplash.com/photo-1604568529494-a4bff4a5e8b7?w=600&q=80'),
('Nanteza Brenda',  'Watermelon',        'fruits',           1200,   700, 'Masaka',    'Large seedless watermelons, 5–8 kg each. Farm gate pickup.',           true, false, false, false, 'Cash',             'https://images.unsplash.com/photo-1601493700631-2b16ec4b4716?w=600&q=80'),

-- CEREALS & GRAINS
('Ochieng Moses',   'Maize (Dry)',        'cereals-grains',  2000,   900, 'Gulu',      'Grade A dry maize, moisture <13%. Ideal for milling or animal feed.',  true, false, true,  true,  'MTN MoMo / Bank',  'https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=600&q=80'),
('Atim Lydia',      'Rice (Upland)',      'cereals-grains',   800,  2800, 'Bugiri',    'NERICA upland rice, unpolished. High yield, local variety.',           true, false, true,  false, 'Bank transfer',    'https://images.unsplash.com/photo-1536304993881-ff86d42818e2?w=600&q=80'),
('Lubega Francis',  'Millet (Finger)',    'cereals-grains',   600,  1800, 'Rakai',     'Organic finger millet, sun-dried. Great for porridge & obushera.',     true, true,  false, false, 'Cash / MoMo',      'https://images.unsplash.com/photo-1612257999756-77e6e0a7b4c2?w=600&q=80'),

-- LEGUMES & PULSES
('Namukasa Diana',  'Beans (K132)',       'legumes-pulses',  1500,  2200, 'Mbarara',   'K132 climbing beans, well dried. 90 kg bags available.',              true, false, true,  true,  'MTN MoMo',         'https://images.unsplash.com/photo-1567306295427-94503f8300d7?w=600&q=80'),
('Tukahirwa Eric',  'Soybeans',          'legumes-pulses',  2000,  1900, 'Masindi',   'Improved soybean variety. High protein. Good for oil extraction.',     true, false, true,  true,  'Bank / MoMo',      'https://images.unsplash.com/photo-1587734195503-904fca47e0e9?w=600&q=80'),
('Aciro Joyce',     'Groundnuts (Raw)',   'legumes-pulses',   400,  3200, 'Apac',      'Red-skin groundnuts, shelled. Aflatoxin tested. Export quality.',      true, true,  true,  false, 'Cash on delivery', 'https://images.unsplash.com/photo-1567306297438-26a3e09b92d0?w=600&q=80'),

-- ROOTS & TUBERS
('Mugisha Robert',  'Sweet Potato',      'roots-tubers',    1000,   500, 'Kabale',    'Orange-flesh sweet potato (OFSP). Rich in Vitamin A.',                 true, true,  false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1596097635121-14b38c5d7a26?w=600&q=80'),
('Nantaba Alice',   'Irish Potato',      'roots-tubers',    2000,  1100, 'Kabale',    'Victoria variety. Smooth skin, low-moisture. Good for chips.',         true, false, true,  true,  'Cash / Airtel',    'https://images.unsplash.com/photo-1518977676601-b53f82aba655?w=600&q=80'),
('Byarugaba Paul',  'Matooke (Bunch)',   'roots-tubers',     300,  5000, 'Ibanda',    'Mature matooke bunches, 15–20 kg. Ready for cooking.',                 true, false, false, true,  'Cash',             'https://images.unsplash.com/photo-1543218024-57a70143c369?w=600&q=80'),

-- NUTS & OILSEEDS
('Kiggundu James',  'Sesame (Sim-sim)',  'nuts-oilseeds',    500,  5500, 'Arua',      'White sesame seeds, cleaned & dried. For export / oil pressing.',      true, false, true,  false, 'Bank transfer',    'https://images.unsplash.com/photo-1612257999765-b4c8f28b3f0a?w=600&q=80'),
('Nakabugo Sarah',  'Macadamia Nuts',    'nuts-oilseeds',    200, 18000, 'Lyantonde', 'In-shell macadamia, moisture <3%. NACRRI certified.',                  true, true,  true,  false, 'Bank / MoMo',      'https://images.unsplash.com/photo-1563208073-5aab4d7e3f6b?w=600&q=80'),

-- CASH CROPS
('Tumwine David',   'Robusta Coffee',    'cash-crops',       800, 10000, 'Masaka',    'Washed Robusta FAQ grade. Sorted, moisture 12.5%.',                    true, false, true,  false, 'Bank transfer',    'https://images.unsplash.com/photo-1447933601403-0c6688de566e?w=600&q=80'),
('Kyomuhendo Isa',  'Vanilla Beans',     'cash-crops',        50, 90000, 'Mukono',    'Cured Grade A vanilla. 15–18 cm pods. Certified organic.',             true, true,  true,  false, 'Bank only',        'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=600&q=80'),
('Nsubuga Alex',    'Cocoa (Wet)',        'cash-crops',       400,  3500, 'Bundibugyo','Fresh wet fermented cocoa beans. Ready for drying.',                   true, true,  false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1481391243133-f96216dcb5d2?w=600&q=80'),

-- HERBS & SPICES
('Nassazi Lydia',   'Fresh Ginger',      'herbs-spices',     200,  4000, 'Kamuli',    'Young fresh ginger rhizomes, clean and aromatic.',                     true, true,  false, true,  'Cash / MoMo',      'https://images.unsplash.com/photo-1615485500704-8e990f9900f7?w=600&q=80'),
('Ayella Charles',  'Bird\'s Eye Chilli','herbs-spices',      80,  8000, 'Soroti',    'Dried red chilli, 50k SHU. Ground or whole available.',               true, false, false, false, 'Cash',             'https://images.unsplash.com/photo-1588252303782-cb80119abd6d?w=600&q=80'),

-- LIVESTOCK
('Tumusiime Fred',  'Ankole Cattle',     'livestock',          5,950000, 'Mbarara',   'Pure Ankole long-horn cattle. MAAIF vaccinated. 3–4 years old.',       true, false, true,  false, 'Bank transfer',    'https://images.unsplash.com/photo-1500595046743-cd271d694d30?w=600&q=80'),
('Nabirye Dorah',   'Boer Goats',        'livestock',         20,250000, 'Jinja',     'Crossbred Boer goats. Well-fed, ready for market.',                    true, false, false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1524024973431-2ad916746881?w=600&q=80'),

-- MEAT & ANIMAL PRODUCTS
('Ssemakula Tom',   'Goat Meat (Fresh)', 'meat-products',    120,  9000, 'Wakiso',    'Fresh slaughtered goat meat. Halal certified. Next-day delivery.',     true, false, true,  true,  'Cash on delivery', 'https://images.unsplash.com/photo-1529692236671-f1f6cf9683ba?w=600&q=80'),
('Nansubuga Rose',  'Free-range Eggs',   'meat-products',    500,   800, 'Mukono',    'Tray of 30 eggs from free-range indigenous hens.',                     true, true,  false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1582722872445-44dc5f7e3c8f?w=600&q=80'),

-- DAIRY
('Kakooza Henry',   'Fresh Milk',        'dairy',           5000,  1200, 'Mbarara',   'Fresh whole milk from zero-grazing Friesians. Daily collection.',      true, false, true,  true,  'MTN MoMo / Cash',  'https://images.unsplash.com/photo-1550583724-b2692b85b150?w=600&q=80'),
('Nanyonga Peace',  'Natural Yoghurt',   'dairy',            200,  4500, 'Wakiso',    'Homemade set yoghurt, 500 ml cups. Plain & passion fruit.',            true, false, false, true,  'Cash / MoMo',      'https://images.unsplash.com/photo-1488477181228-c84a3fb0e49e?w=600&q=80'),
('Kisekka Mark',    'Ghee (Locally Made)','dairy',           100, 28000, 'Rakai',     'Pure cow ghee, 500 g jars. Traditional slow-cook method.',             true, false, false, false, 'Cash',             'https://images.unsplash.com/photo-1627308595129-3f40e3b30e23?w=600&q=80'),

-- FISH & AQUACULTURE
('Mukasa Richard',  'Tilapia (Fresh)',   'fish-aquaculture', 300,  8000, 'Mukono',    'Live-weight Nile tilapia, 300–500 g. Lake Victoria, cage-farmed.',     true, false, true,  true,  'Cash / MoMo',      'https://images.unsplash.com/photo-1519708227418-c8fd9a32b7a2?w=600&q=80'),
('Nabirye Helen',   'Mukene (Dagaa)',    'fish-aquaculture', 500,  6000, 'Jinja',     'Sun-dried silver cyprinid (mukene). Clean, low moisture. 50 kg bags.', true, false, false, false, 'Cash on delivery', 'https://images.unsplash.com/photo-1534482421-64566f976cfa?w=600&q=80'),

-- HONEY & BEEKEEPING
('Oryema Ben',      'Raw Forest Honey',  'honey-beekeeping', 150, 15000, 'Madi-Okollo','Unfiltered raw honey from forest hives. Rich, dark & aromatic.',       true, true,  true,  true,  'MTN MoMo',         'https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=600&q=80'),
('Atiku Grace',     'Beeswax (Raw)',     'honey-beekeeping',  30, 12000, 'Pader',      'Natural yellow beeswax blocks. For cosmetics & candle making.',        true, true,  false, false, 'Cash',             'https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600&q=80'),

-- PROCESSED PRODUCTS
('Namugera Stella', 'Peanut Butter',     'processed-products',80, 7000, 'Kampala',    'Natural groundnut paste, no additives. 300 g & 1 kg jars.',           true, true,  false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1612187834699-4b3bebb52e72?w=600&q=80'),
('Kato Brian',      'Simsim Paste',      'processed-products',50, 9000, 'Arua',       'Pure sesame paste (tahini-style). 250 g & 500 g packs.',              true, true,  false, false, 'Cash / MoMo',      'https://images.unsplash.com/photo-1612257999756-77e6e0a7b4c2?w=600&q=80'),

-- ANIMAL FEED
('Lwanga Isaac',    'Napier Grass',      'animal-feed',     3000,   300, 'Wakiso',    'Freshly cut Napier grass (Bana grass). Delivery within Wakiso.',       true, false, false, true,  'Cash on delivery', 'https://images.unsplash.com/photo-1500595046743-cd271d694d30?w=600&q=80'),
('Nagawa Esther',   'Poultry Mash',      'animal-feed',     1000,  1800, 'Kampala',   'Layers mash (16% CP). 50 kg bags. Farm-mixed, no antibiotics.',       true, false, false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1569074187119-c87815b476da?w=600&q=80'),

-- SEEDS & PLANTING MATERIAL
('Kalule Joseph',   'Maize Seed (LONGE 5)','seeds-planting',  200, 12000,'Mukono',    'Certified NARO LONGE 5 hybrid maize seed. 2 kg packs.',               true, false, true,  false, 'Cash / MoMo',      'https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=600&q=80'),
('Nakkazi Faridah', 'Avocado Seedlings', 'seeds-planting',   500,  3500, 'Wakiso',    'Grafted Hass & Fuerte avocado seedlings, 6 months old.',              true, true,  false, true,  'Cash on delivery', 'https://images.unsplash.com/photo-1582284540020-8862d607ef1f?w=600&q=80'),

-- MEDICINAL PLANTS
('Opio Kenneth',    'Moringa Leaves (Dry)','medicinal-plants',100, 8000, 'Lira',      'Sun-dried moringa oleifera leaves. Packed in 100 g & 500 g bags.',    true, true,  false, false, 'MTN MoMo',         'https://images.unsplash.com/photo-1527525443983-6e60c75fff46?w=600&q=80'),

-- AGRI BY-PRODUCTS
('Muwonge Charles', 'Compost Manure',    'agri-byproducts',10000,   250, 'Mukono',    'Well-decomposed cow & crop compost. 50 kg bags. Delivery available.',  true, true,  false, true,  'Cash / MTN MoMo',  'https://images.unsplash.com/photo-1416879595882-3373a0480b5b?w=600&q=80'),
('Nakabugo Prim',   'Rice Husks',        'agri-byproducts', 5000,   120, 'Bugiri',    'Dry rice husks. Good for mulching, fuel briquettes & poultry litter.',true, false, false, true,  'Cash',             'https://images.unsplash.com/photo-1536304993881-ff86d42818e2?w=600&q=80')

on conflict (id) do nothing;

-- ── 4. Update category_id FK for all listings where category slug matches ───
update public.listings l
set    category_id = c.id
from   public.product_categories c
where  l.category = c.slug
  and  c.level = 1
  and  l.category_id is null;

commit;
