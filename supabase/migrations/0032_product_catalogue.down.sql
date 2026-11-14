-- Rollback 0032
begin;
drop view  if exists public.v_category_tree;
alter table public.listings drop column if exists category_id;
drop table if exists public.product_categories cascade;
commit;
