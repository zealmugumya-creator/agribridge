-- AgriBridge migration 0032 — Master Product Catalogue
-- Creates a full 20-category hierarchical catalogue (main category → subcategory → product type)
-- Shared across the entire platform. Farmers pick from this catalogue when listing produce.
-- Public read access; write restricted to service_role.

begin;

-- ── 1. CATEGORIES TABLE ──────────────────────────────────────────────────────
create table if not exists public.product_categories (
  id            uuid primary key default gen_random_uuid(),
  parent_id     uuid references public.product_categories(id) on delete cascade,
  slug          text not null unique,          -- machine-readable, e.g. 'vegetables-leafy'
  name          text not null,                 -- display name
  level         smallint not null default 1,   -- 1=main, 2=subcategory
  sort_order    smallint not null default 0,
  icon_emoji    text,
  hero_img_url  text,                          -- Unsplash CDN URL for browse page
  description   text,
  is_active     boolean not null default true,
  created_at    timestamptz not null default now()
);

comment on table public.product_categories is
  'Hierarchical product catalogue shared across all AgriBridge listings.
   level 1 = main marketplace category, level 2 = subcategory.
   Products within a subcategory are stored as crop_name on listings.';

create index if not exists idx_product_categories_parent on public.product_categories(parent_id);
create index if not exists idx_product_categories_slug   on public.product_categories(slug);
create index if not exists idx_product_categories_active on public.product_categories(is_active);

-- ── 2. LINK LISTINGS TO CATALOGUE ───────────────────────────────────────────
alter table public.listings add column if not exists category_id uuid
  references public.product_categories(id) on delete set null;
comment on column public.listings.category_id is
  'Catalogue category this listing belongs to. The existing `category` text column
   remains for backwards compat with old API calls.';
create index if not exists idx_listings_category_id on public.listings(category_id);

-- ── 3. RLS ───────────────────────────────────────────────────────────────────
alter table public.product_categories enable row level security;

-- Everyone (even anon) can read categories
drop policy if exists "categories_public_read" on public.product_categories;
create policy "categories_public_read"
  on public.product_categories for select
  using (true);

-- Only service_role can write (farmers pick from fixed list; admins manage via service key)
drop policy if exists "categories_service_write" on public.product_categories;
create policy "categories_service_write"
  on public.product_categories for all
  to service_role
  using (true)
  with check (true);

-- ── 4. SEED: MAIN CATEGORIES (level 1) ──────────────────────────────────────
-- Uses INSERT ... ON CONFLICT DO NOTHING so reruns are safe.
insert into public.product_categories (slug, name, level, sort_order, icon_emoji, hero_img_url, description) values
  ('vegetables',        'Vegetables',                1,  1,  '🥦', 'https://images.unsplash.com/photo-1540420773420-3366772f4999?w=800&q=80', 'Fresh vegetables of all kinds'),
  ('fruits',            'Fruits',                    1,  2,  '🍋', 'https://images.unsplash.com/photo-1619566636858-adf3ef46400b?w=800&q=80', 'Fresh tropical and other fruits'),
  ('cereals-grains',    'Cereals and Grains',        1,  3,  '🌾', 'https://images.unsplash.com/photo-1574323347407-f5e1ad6d020b?w=800&q=80', 'Maize, rice, millet, sorghum and more'),
  ('legumes-pulses',    'Legumes and Pulses',        1,  4,  '🫘', 'https://images.unsplash.com/photo-1515543904379-3d757afe72e4?w=800&q=80', 'Beans, cowpeas, soybeans and other pulses'),
  ('roots-tubers',      'Roots, Tubers and Bananas', 1,  5,  '🍠', 'https://images.unsplash.com/photo-1518977676601-b53f82aba655?w=800&q=80', 'Cassava, sweet potato, Irish potato, matooke'),
  ('nuts-oilseeds',     'Nuts and Oilseeds',         1,  6,  '🥜', 'https://images.unsplash.com/photo-1574323347407-f5e1ad6d020b?w=800&q=80', 'Groundnuts, sesame, sunflower and more'),
  ('cash-crops',        'Cash and Industrial Crops', 1,  7,  '☕', 'https://images.unsplash.com/photo-1447933601403-0c6688de566e?w=800&q=80', 'Coffee, tea, cocoa, cotton and other cash crops'),
  ('herbs-spices',      'Herbs and Spices',          1,  8,  '🌿', 'https://images.unsplash.com/photo-1466637574441-749b8f19452f?w=800&q=80', 'Fresh herbs, dried spices and aromatics'),
  ('livestock',         'Livestock and Farm Animals',1,  9,  '🐄', 'https://images.unsplash.com/photo-1500595046743-cd271d694d30?w=800&q=80', 'Cattle, goats, sheep, pigs and poultry'),
  ('meat-products',     'Meat and Animal Products',  1, 10,  '🥩', 'https://images.unsplash.com/photo-1529692236671-f1f6cf9683ba?w=800&q=80', 'Fresh meat, eggs, hides and other animal products'),
  ('dairy',             'Dairy and Milk Products',   1, 11,  '🥛', 'https://images.unsplash.com/photo-1550583724-b2692b85b150?w=800&q=80', 'Milk, yoghurt, cheese, butter and other dairy'),
  ('fish-aquaculture',  'Fish and Aquaculture',      1, 12,  '🐟', 'https://images.unsplash.com/photo-1534482421-64566f976cfa?w=800&q=80', 'Tilapia, Nile perch, mukene and other fish'),
  ('honey-beekeeping',  'Honey and Beekeeping',      1, 13,  '🍯', 'https://images.unsplash.com/photo-1587049352846-4a222e784d38?w=800&q=80', 'Raw honey, beeswax, propolis and bee products'),
  ('flowers-horticulture','Flowers and Horticulture',1, 14,  '🌸', 'https://images.unsplash.com/photo-1487530811015-780f492b2fb0?w=800&q=80', 'Cut flowers, ornamentals and seedlings'),
  ('processed-products','Processed Farm Products',   1, 15,  '🫙', 'https://images.unsplash.com/photo-1495195134817-aeb325a55b65?w=800&q=80', 'Flour, oils, dried products and value-added goods'),
  ('animal-feed',       'Animal Feed and Fodder',    1, 16,  '🌱', 'https://images.unsplash.com/photo-1500595046743-cd271d694d30?w=800&q=80', 'Grass, hay, silage, meal and feed supplements'),
  ('seeds-planting',    'Seeds and Planting Materials',1,17, '🌰', 'https://images.unsplash.com/photo-1416879595882-3373a0480b5b?w=800&q=80', 'Crop seeds, cuttings, suckers and seedlings'),
  ('forestry',          'Forestry and Agroforestry', 1, 18,  '🪵', 'https://images.unsplash.com/photo-1448375240586-882707db888b?w=800&q=80', 'Timber, bamboo, firewood and forest products'),
  ('medicinal-plants',  'Medicinal and Aromatic Plants',1,19,'🌿','https://images.unsplash.com/photo-1515023115689-589c33041d3c?w=800&q=80', 'Aloe vera, moringa, neem and herbal plant materials'),
  ('agri-byproducts',   'Agricultural By-products',  1, 20,  '♻️', 'https://images.unsplash.com/photo-1416879595882-3373a0480b5b?w=800&q=80', 'Manure, compost, crop residues and organic materials')
on conflict (slug) do nothing;

-- ── 5. SEED: SUBCATEGORIES (level 2) ─────────────────────────────────────────
-- Vegetables
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'veg-leafy',    'Leafy Vegetables',          2, 1  from public.product_categories where slug='vegetables' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'veg-fruiting', 'Fruiting Vegetables',       2, 2  from public.product_categories where slug='vegetables' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'veg-roots',    'Root and Tuber Vegetables', 2, 3  from public.product_categories where slug='vegetables' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'veg-bulbs',    'Bulb Vegetables',           2, 4  from public.product_categories where slug='vegetables' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'veg-flowers',  'Flower and Stem Vegetables',2, 5  from public.product_categories where slug='vegetables' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'veg-pods',     'Pod Vegetables',            2, 6  from public.product_categories where slug='vegetables' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'veg-mushrooms','Mushrooms',                 2, 7  from public.product_categories where slug='vegetables' on conflict (slug) do nothing;

-- Fruits
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fruit-bananas', 'Bananas',           2, 1 from public.product_categories where slug='fruits' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fruit-mangoes', 'Mangoes',           2, 2 from public.product_categories where slug='fruits' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fruit-citrus',  'Citrus Fruits',     2, 3 from public.product_categories where slug='fruits' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fruit-tropical','Tropical Fruits',   2, 4 from public.product_categories where slug='fruits' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fruit-melons',  'Melons',            2, 5 from public.product_categories where slug='fruits' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fruit-berries', 'Berries',           2, 6 from public.product_categories where slug='fruits' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fruit-other',   'Other Fruits',      2, 7 from public.product_categories where slug='fruits' on conflict (slug) do nothing;

-- Legumes
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'legume-beans',  'Beans',       2, 1 from public.product_categories where slug='legumes-pulses' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'legume-other',  'Other Pulses',2, 2 from public.product_categories where slug='legumes-pulses' on conflict (slug) do nothing;

-- Nuts
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'nuts-nuts',     'Nuts',     2, 1 from public.product_categories where slug='nuts-oilseeds' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'nuts-oilseed',  'Oilseeds', 2, 2 from public.product_categories where slug='nuts-oilseeds' on conflict (slug) do nothing;

-- Herbs
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'herb-fresh',    'Fresh Herbs', 2, 1 from public.product_categories where slug='herbs-spices' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'herb-spices',   'Spices',      2, 2 from public.product_categories where slug='herbs-spices' on conflict (slug) do nothing;

-- Livestock
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'live-cattle',   'Cattle',           2, 1 from public.product_categories where slug='livestock' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'live-goats',    'Goats',            2, 2 from public.product_categories where slug='livestock' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'live-sheep',    'Sheep',            2, 3 from public.product_categories where slug='livestock' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'live-pigs',     'Pigs',             2, 4 from public.product_categories where slug='livestock' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'live-poultry',  'Poultry',          2, 5 from public.product_categories where slug='livestock' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'live-other',    'Other Farm Animals',2,6 from public.product_categories where slug='livestock' on conflict (slug) do nothing;

-- Meat
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'meat-meat',     'Meat',              2, 1 from public.product_categories where slug='meat-products' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'meat-eggs',     'Eggs',              2, 2 from public.product_categories where slug='meat-products' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'meat-other',    'Other Animal Products',2,3 from public.product_categories where slug='meat-products' on conflict (slug) do nothing;

-- Dairy
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'dairy-milk',    'Milk',           2, 1 from public.product_categories where slug='dairy' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'dairy-products','Dairy Products', 2, 2 from public.product_categories where slug='dairy' on conflict (slug) do nothing;

-- Fish
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fish-fresh',    'Freshwater Fish',      2, 1 from public.product_categories where slug='fish-aquaculture' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fish-products', 'Fish Products',        2, 2 from public.product_categories where slug='fish-aquaculture' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'fish-aqua',     'Aquaculture Products', 2, 3 from public.product_categories where slug='fish-aquaculture' on conflict (slug) do nothing;

-- Flowers
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'flower-cut',    'Cut Flowers',               2, 1 from public.product_categories where slug='flowers-horticulture' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'flower-ornamental','Ornamental Plants',       2, 2 from public.product_categories where slug='flowers-horticulture' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'flower-seedlings','Horticultural Seedlings', 2, 3 from public.product_categories where slug='flowers-horticulture' on conflict (slug) do nothing;

-- Processed
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'proc-flour',    'Flours and Meals',           2, 1 from public.product_categories where slug='processed-products' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'proc-dried',    'Dried Products',             2, 2 from public.product_categories where slug='processed-products' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'proc-oils',     'Oils and Fats',              2, 3 from public.product_categories where slug='processed-products' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'proc-juices',   'Fruit and Vegetable Products',2,4 from public.product_categories where slug='processed-products' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'proc-other',    'Other Processed Products',   2, 5 from public.product_categories where slug='processed-products' on conflict (slug) do nothing;

-- Animal Feed
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'feed-fodder',   'Grass and Fodder',    2, 1 from public.product_categories where slug='animal-feed' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'feed-meal',     'Meals and Feed',      2, 2 from public.product_categories where slug='animal-feed' on conflict (slug) do nothing;

-- Seeds
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'seed-crop',     'Crop Seeds',          2, 1 from public.product_categories where slug='seeds-planting' on conflict (slug) do nothing;
insert into public.product_categories (parent_id, slug, name, level, sort_order) select id, 'seed-planting', 'Planting Materials',  2, 2 from public.product_categories where slug='seeds-planting' on conflict (slug) do nothing;

-- ── 6. HELPER VIEW: flat list useful for dropdowns ───────────────────────────
create or replace view public.v_category_tree as
select
  c.id,
  c.slug,
  c.name,
  c.level,
  c.sort_order,
  c.icon_emoji,
  c.hero_img_url,
  c.description,
  c.is_active,
  c.parent_id,
  p.name  as parent_name,
  p.slug  as parent_slug
from public.product_categories c
left join public.product_categories p on p.id = c.parent_id
order by coalesce(p.sort_order, c.sort_order), c.sort_order;

-- ── 7. GRANT ─────────────────────────────────────────────────────────────────
grant select on public.product_categories   to anon, authenticated;
grant select on public.v_category_tree      to anon, authenticated;

commit;
