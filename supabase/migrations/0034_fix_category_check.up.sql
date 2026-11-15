-- Migration 0034: Drop old category check constraint and replace with one
-- that allows all 20 catalogue slugs plus legacy values.

begin;

-- Drop the old restrictive check
alter table public.listings drop constraint if exists listings_category_check;

-- Add new constraint that accepts all current catalogue slugs + legacy values
alter table public.listings
  add constraint listings_category_check check (
    category is null or category in (
      -- 20 catalogue slugs
      'vegetables','fruits','cereals-grains','legumes-pulses','roots-tubers',
      'nuts-oilseeds','cash-crops','herbs-spices','livestock','meat-products',
      'dairy','fish-aquaculture','honey-beekeeping','flowers-horticulture',
      'processed-products','animal-feed','seeds-planting','forestry',
      'medicinal-plants','agri-byproducts',
      -- legacy values that may already exist in the table
      'grains','staples','organic','poultry','aqua',
      'vegetables','fruits','cereals','legumes','roots',
      'nuts','cash crops','herbs','meat','dairy','fish','honey',
      'flowers','processed','feed','seeds','medicinal','byproducts'
    )
  );

commit;
