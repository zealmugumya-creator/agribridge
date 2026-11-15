-- Migration 0033: Demo listings — 40 realistic Ugandan farm products across all 20 categories
-- Inserts demo farmer profiles first, then links listings via farmer_id FK.
-- Safe to re-run (ON CONFLICT DO NOTHING everywhere).

begin;

-- ── 0. Fix existing 4 listings to correct category slugs ───────────────────
update public.listings set category = 'fruits'       where lower(crop_name) like '%avocado%';
update public.listings set category = 'roots-tubers' where lower(crop_name) like '%cassava%';
update public.listings set category = 'fruits'       where lower(crop_name) like '%mango%';
update public.listings set category = 'cash-crops'   where lower(crop_name) like '%coffee%';

-- ── 1. Demo farmers (one per region, shared across listings) ───────────────
insert into public.farmers (id, full_name, phone, district, village, is_verified, created_at)
values
  ('a1000000-0000-0000-0000-000000000001', 'Nakato Grace',       '+256770000001', 'Wakiso',       'Busukuma',    true,  now()),
  ('a1000000-0000-0000-0000-000000000002', 'Ochieng Moses',      '+256770000002', 'Gulu',         'Layibi',      true,  now()),
  ('a1000000-0000-0000-0000-000000000003', 'Mugisha Robert',     '+256770000003', 'Kabale',       'Kamwezi',     true,  now()),
  ('a1000000-0000-0000-0000-000000000004', 'Namukasa Diana',     '+256770000004', 'Mbarara',      'Rubindi',     true,  now()),
  ('a1000000-0000-0000-0000-000000000005', 'Tumwine David',      '+256770000005', 'Masaka',       'Kyabakuza',   true,  now()),
  ('a1000000-0000-0000-0000-000000000006', 'Kakooza Henry',      '+256770000006', 'Mbarara',      'Nkokonjeru',  true,  now()),
  ('a1000000-0000-0000-0000-000000000007', 'Mukasa Richard',     '+256770000007', 'Jinja',        'Nalufenya',   true,  now()),
  ('a1000000-0000-0000-0000-000000000008', 'Oryema Ben',         '+256770000008', 'Madi-Okollo',  'Paidha',      false, now()),
  ('a1000000-0000-0000-0000-000000000009', 'Namugera Stella',    '+256770000009', 'Kampala',      'Kawempe',     false, now()),
  ('a1000000-0000-0000-0000-000000000010', 'Lwanga Isaac',       '+256770000010', 'Wakiso',       'Nansana',     false, now())
on conflict (id) do nothing;

-- ── 2. Demo listings ────────────────────────────────────────────────────────
insert into public.listings
  (farmer_id, crop_name, category, quantity_kg, price_per_kg, district,
   description, is_available, is_organic, is_verified,
   delivery_available, payment_terms, image_url)
values

-- VEGETABLES (farmer: Nakato Grace)
('a1000000-0000-0000-0000-000000000001', 'Tomatoes',          'vegetables',        500,  1500, 'Wakiso',       'Fresh Roma tomatoes, picked daily. No pesticides used.',                true, true,  true,  true,  'MTN MoMo / Cash',  'https://images.unsplash.com/photo-1546094096-0df4bcaaa337?w=600&q=80'),
('a1000000-0000-0000-0000-000000000001', 'Cabbage',           'vegetables',        300,   800, 'Mukono',       'Large firm cabbages, 1–2 kg each. Available year-round.',               true, false, true,  false, 'Cash on delivery', 'https://images.unsplash.com/photo-1594282486552-05b4d80fbb9f?w=600&q=80'),
('a1000000-0000-0000-0000-000000000001', 'Green Pepper',      'vegetables',        150,  2500, 'Lira',         'Sweet green bell peppers, greenhouse grown.',                           true, false, false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1563565375-f3fdfdbefa83?w=600&q=80'),
('a1000000-0000-0000-0000-000000000001', 'Onions',            'vegetables',        800,  1200, 'Kasese',       'Red onions, dry-cured. Bulk orders welcome.',                           true, false, true,  true,  'Airtel Money',     'https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=600&q=80'),

-- FRUITS
('a1000000-0000-0000-0000-000000000001', 'Pineapple',         'fruits',            400,   600, 'Kayunga',      'Sweet Smooth Cayenne pineapples. Order by crate (20 pcs).',             true, true,  true,  true,  'MTN MoMo',         'https://images.unsplash.com/photo-1550258987-190a2d41a8ba?w=600&q=80'),
('a1000000-0000-0000-0000-000000000001', 'Passion Fruit',     'fruits',            200,  3500, 'Soroti',       'Purple passion fruit, high juice content. Ready for export.',           true, true,  false, true,  'Cash / MoMo',      'https://images.unsplash.com/photo-1604568529494-a4bff4a5e8b7?w=600&q=80'),
('a1000000-0000-0000-0000-000000000001', 'Watermelon',        'fruits',           1200,   700, 'Masaka',       'Large seedless watermelons, 5–8 kg each. Farm gate pickup.',            true, false, false, false, 'Cash',             'https://images.unsplash.com/photo-1601493700631-2b16ec4b4716?w=600&q=80'),

-- CEREALS & GRAINS
('a1000000-0000-0000-0000-000000000002', 'Maize Dry',         'cereals-grains',   2000,   900, 'Gulu',         'Grade A dry maize, moisture <13%. Ideal for milling or animal feed.',  true, false, true,  true,  'MTN MoMo / Bank',  'https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=600&q=80'),
('a1000000-0000-0000-0000-000000000002', 'Upland Rice',       'cereals-grains',    800,  2800, 'Bugiri',       'NERICA upland rice, unpolished. High yield, local variety.',            true, false, true,  false, 'Bank transfer',    'https://images.unsplash.com/photo-1536304993881-ff86d42818e2?w=600&q=80'),
('a1000000-0000-0000-0000-000000000002', 'Finger Millet',     'cereals-grains',    600,  1800, 'Rakai',        'Organic finger millet, sun-dried. Great for porridge and obushera.',   true, true,  false, false, 'Cash / MoMo',      'https://images.unsplash.com/photo-1612257999756-77e6e0a7b4c2?w=600&q=80'),

-- LEGUMES & PULSES
('a1000000-0000-0000-0000-000000000004', 'Beans K132',        'legumes-pulses',   1500,  2200, 'Mbarara',      'K132 climbing beans, well dried. 90 kg bags available.',               true, false, true,  true,  'MTN MoMo',         'https://images.unsplash.com/photo-1567306295427-94503f8300d7?w=600&q=80'),
('a1000000-0000-0000-0000-000000000004', 'Soybeans',          'legumes-pulses',   2000,  1900, 'Masindi',      'Improved soybean variety. High protein. Good for oil extraction.',     true, false, true,  true,  'Bank / MoMo',      'https://images.unsplash.com/photo-1587734195503-904fca47e0e9?w=600&q=80'),
('a1000000-0000-0000-0000-000000000004', 'Raw Groundnuts',    'legumes-pulses',    400,  3200, 'Apac',         'Red-skin groundnuts, shelled. Aflatoxin tested. Export quality.',      true, true,  true,  false, 'Cash on delivery', 'https://images.unsplash.com/photo-1567306297438-26a3e09b92d0?w=600&q=80'),

-- ROOTS & TUBERS
('a1000000-0000-0000-0000-000000000003', 'Sweet Potato OFSP', 'roots-tubers',     1000,   500, 'Kabale',       'Orange-flesh sweet potato (OFSP). Rich in Vitamin A.',                 true, true,  false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1596097635121-14b38c5d7a26?w=600&q=80'),
('a1000000-0000-0000-0000-000000000003', 'Irish Potato',      'roots-tubers',     2000,  1100, 'Kabale',       'Victoria variety. Smooth skin, low-moisture. Good for chips.',         true, false, true,  true,  'Cash / Airtel',    'https://images.unsplash.com/photo-1518977676601-b53f82aba655?w=600&q=80'),
('a1000000-0000-0000-0000-000000000003', 'Matooke Bunch',     'roots-tubers',      300,  5000, 'Ibanda',       'Mature matooke bunches, 15–20 kg. Ready for cooking.',                 true, false, false, true,  'Cash',             'https://images.unsplash.com/photo-1543218024-57a70143c369?w=600&q=80'),

-- NUTS & OILSEEDS
('a1000000-0000-0000-0000-000000000002', 'Sesame Sim-sim',    'nuts-oilseeds',     500,  5500, 'Arua',         'White sesame seeds, cleaned and dried. For export or oil pressing.',   true, false, true,  false, 'Bank transfer',    'https://images.unsplash.com/photo-1612257999765-b4c8f28b3f0a?w=600&q=80'),
('a1000000-0000-0000-0000-000000000002', 'Macadamia Nuts',    'nuts-oilseeds',     200, 18000, 'Lyantonde',    'In-shell macadamia, moisture <3%. NACRRI certified.',                  true, true,  true,  false, 'Bank / MoMo',      'https://images.unsplash.com/photo-1563208073-5aab4d7e3f6b?w=600&q=80'),

-- CASH CROPS
('a1000000-0000-0000-0000-000000000005', 'Robusta Coffee',    'cash-crops',        800, 10000, 'Masaka',       'Washed Robusta FAQ grade. Sorted, moisture 12.5%.',                    true, false, true,  false, 'Bank transfer',    'https://images.unsplash.com/photo-1447933601403-0c6688de566e?w=600&q=80'),
('a1000000-0000-0000-0000-000000000005', 'Vanilla Beans',     'cash-crops',         50, 90000, 'Mukono',       'Cured Grade A vanilla. 15–18 cm pods. Certified organic.',             true, true,  true,  false, 'Bank only',        'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=600&q=80'),
('a1000000-0000-0000-0000-000000000005', 'Wet Cocoa Beans',   'cash-crops',        400,  3500, 'Bundibugyo',   'Fresh wet fermented cocoa beans. Ready for drying.',                   true, true,  false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1481391243133-f96216dcb5d2?w=600&q=80'),

-- HERBS & SPICES
('a1000000-0000-0000-0000-000000000001', 'Fresh Ginger',      'herbs-spices',      200,  4000, 'Kamuli',       'Young fresh ginger rhizomes, clean and aromatic.',                     true, true,  false, true,  'Cash / MoMo',      'https://images.unsplash.com/photo-1615485500704-8e990f9900f7?w=600&q=80'),
('a1000000-0000-0000-0000-000000000001', 'Birds Eye Chilli',  'herbs-spices',       80,  8000, 'Soroti',       'Dried red chilli, 50k SHU. Ground or whole available.',                true, false, false, false, 'Cash',             'https://images.unsplash.com/photo-1588252303782-cb80119abd6d?w=600&q=80'),

-- LIVESTOCK
('a1000000-0000-0000-0000-000000000004', 'Ankole Cattle',     'livestock',           5,950000, 'Mbarara',     'Pure Ankole long-horn cattle. MAAIF vaccinated. 3–4 years old.',       true, false, true,  false, 'Bank transfer',    'https://images.unsplash.com/photo-1500595046743-cd271d694d30?w=600&q=80'),
('a1000000-0000-0000-0000-000000000004', 'Boer Goats',        'livestock',          20,250000, 'Jinja',       'Crossbred Boer goats. Well-fed, ready for market.',                    true, false, false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1524024973431-2ad916746881?w=600&q=80'),

-- MEAT & ANIMAL PRODUCTS
('a1000000-0000-0000-0000-000000000005', 'Fresh Goat Meat',   'meat-products',     120,  9000, 'Wakiso',       'Fresh slaughtered goat meat. Halal certified. Next-day delivery.',     true, false, true,  true,  'Cash on delivery', 'https://images.unsplash.com/photo-1529692236671-f1f6cf9683ba?w=600&q=80'),
('a1000000-0000-0000-0000-000000000001', 'Free-range Eggs',   'meat-products',     500,   800, 'Mukono',       'Tray of 30 eggs from free-range indigenous hens.',                     true, true,  false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1582722872445-44dc5f7e3c8f?w=600&q=80'),

-- DAIRY
('a1000000-0000-0000-0000-000000000006', 'Fresh Whole Milk',  'dairy',            5000,  1200, 'Mbarara',      'Fresh whole milk from zero-grazing Friesians. Daily collection.',      true, false, true,  true,  'MTN MoMo / Cash',  'https://images.unsplash.com/photo-1550583724-b2692b85b150?w=600&q=80'),
('a1000000-0000-0000-0000-000000000006', 'Natural Yoghurt',   'dairy',             200,  4500, 'Wakiso',       'Homemade set yoghurt, 500 ml cups. Plain and passion fruit.',          true, false, false, true,  'Cash / MoMo',      'https://images.unsplash.com/photo-1488477181228-c84a3fb0e49e?w=600&q=80'),
('a1000000-0000-0000-0000-000000000006', 'Local Ghee',        'dairy',             100, 28000, 'Rakai',        'Pure cow ghee, 500 g jars. Traditional slow-cook method.',             true, false, false, false, 'Cash',             'https://images.unsplash.com/photo-1627308595129-3f40e3b30e23?w=600&q=80'),

-- FISH & AQUACULTURE
('a1000000-0000-0000-0000-000000000007', 'Fresh Tilapia',     'fish-aquaculture',  300,  8000, 'Mukono',       'Live-weight Nile tilapia, 300–500 g. Lake Victoria, cage-farmed.',     true, false, true,  true,  'Cash / MoMo',      'https://images.unsplash.com/photo-1519708227418-c8fd9a32b7a2?w=600&q=80'),
('a1000000-0000-0000-0000-000000000007', 'Dried Mukene',      'fish-aquaculture',  500,  6000, 'Jinja',        'Sun-dried silver cyprinid (mukene). Clean, low moisture. 50 kg bags.', true, false, false, false, 'Cash on delivery', 'https://images.unsplash.com/photo-1534482421-64566f976cfa?w=600&q=80'),

-- HONEY & BEEKEEPING
('a1000000-0000-0000-0000-000000000008', 'Raw Forest Honey',  'honey-beekeeping',  150, 15000, 'Madi-Okollo',  'Unfiltered raw honey from forest hives. Rich, dark and aromatic.',     true, true,  true,  true,  'MTN MoMo',         'https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=600&q=80'),
('a1000000-0000-0000-0000-000000000008', 'Raw Beeswax',       'honey-beekeeping',   30, 12000, 'Pader',        'Natural yellow beeswax blocks. For cosmetics and candle making.',      true, true,  false, false, 'Cash',             'https://images.unsplash.com/photo-1558618666-fcd25c85cd64?w=600&q=80'),

-- PROCESSED PRODUCTS
('a1000000-0000-0000-0000-000000000009', 'Peanut Butter',     'processed-products', 80,  7000, 'Kampala',      'Natural groundnut paste, no additives. 300 g and 1 kg jars.',         true, true,  false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1612187834699-4b3bebb52e72?w=600&q=80'),
('a1000000-0000-0000-0000-000000000009', 'Simsim Paste',      'processed-products', 50,  9000, 'Arua',         'Pure sesame paste. 250 g and 500 g packs.',                            true, true,  false, false, 'Cash / MoMo',      'https://images.unsplash.com/photo-1612257999756-77e6e0a7b4c2?w=600&q=80'),

-- ANIMAL FEED
('a1000000-0000-0000-0000-000000000010', 'Napier Grass',      'animal-feed',      3000,   300, 'Wakiso',       'Freshly cut Napier grass (Bana grass). Delivery within Wakiso.',       true, false, false, true,  'Cash on delivery', 'https://images.unsplash.com/photo-1500595046743-cd271d694d30?w=600&q=80'),
('a1000000-0000-0000-0000-000000000010', 'Layers Mash',       'animal-feed',      1000,  1800, 'Kampala',      'Layers mash (16% CP). 50 kg bags. Farm-mixed, no antibiotics.',        true, false, false, true,  'MTN MoMo',         'https://images.unsplash.com/photo-1569074187119-c87815b476da?w=600&q=80'),

-- SEEDS & PLANTING MATERIAL
('a1000000-0000-0000-0000-000000000002', 'Maize Seed LONGE 5','seeds-planting',    200, 12000, 'Mukono',       'Certified NARO LONGE 5 hybrid maize seed. 2 kg packs.',                true, false, true,  false, 'Cash / MoMo',      'https://images.unsplash.com/photo-1601584115197-04ecc0da31d7?w=600&q=80'),
('a1000000-0000-0000-0000-000000000001', 'Hass Avocado Seedlings','seeds-planting',500,  3500, 'Wakiso',       'Grafted Hass and Fuerte avocado seedlings, 6 months old.',             true, true,  false, true,  'Cash on delivery', 'https://images.unsplash.com/photo-1582284540020-8862d607ef1f?w=600&q=80'),

-- MEDICINAL PLANTS
('a1000000-0000-0000-0000-000000000002', 'Moringa Dry Leaves','medicinal-plants',  100,  8000, 'Lira',         'Sun-dried moringa oleifera leaves. Packed in 100 g and 500 g bags.',  true, true,  false, false, 'MTN MoMo',         'https://images.unsplash.com/photo-1527525443983-6e60c75fff46?w=600&q=80'),

-- AGRI BY-PRODUCTS
('a1000000-0000-0000-0000-000000000010', 'Compost Manure',    'agri-byproducts', 10000,   250, 'Mukono',       'Well-decomposed cow and crop compost. 50 kg bags. Delivery available.', true, true,  false, true,  'Cash / MTN MoMo',  'https://images.unsplash.com/photo-1416879595882-3373a0480b5b?w=600&q=80'),
('a1000000-0000-0000-0000-000000000002', 'Rice Husks',        'agri-byproducts',  5000,   120, 'Bugiri',       'Dry rice husks. Good for mulching, fuel briquettes and poultry litter.',true, false, false, true, 'Cash',             'https://images.unsplash.com/photo-1536304993881-ff86d42818e2?w=600&q=80')

on conflict (id) do nothing;

-- ── 3. Backfill category_id FK for all listings ─────────────────────────────
update public.listings l
set    category_id = c.id
from   public.product_categories c
where  l.category = c.slug
  and  c.level = 1
  and  l.category_id is null;

commit;
