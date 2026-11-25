-- 可配置组合：配置存储（Supabase 项目 strategy-combos）
-- 读：任何人可读（网页、本地信号程序用公开 key 读）
-- 写：只能通过下面的函数，并且要带口令；口令只存 bcrypt 散列

create extension if not exists pgcrypto;

create table if not exists public.combos (
  id         uuid primary key default gen_random_uuid(),
  name       text not null,
  config     jsonb not null,
  note       text default '',
  jq         jsonb default null,          -- 你手填的聚宽实测成绩
  deleted    boolean not null default false,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.app_secret (
  k text primary key,
  v text not null
);

alter table public.combos enable row level security;
alter table public.app_secret enable row level security;      -- 不建任何策略 = 外部完全不可读写

drop policy if exists combos_read on public.combos;
create policy combos_read on public.combos for select using (not deleted);

revoke insert, update, delete on public.combos from anon, authenticated;
revoke all on public.app_secret from anon, authenticated;

-- 口令检查（内部用）
create or replace function public._pass_ok(p text) returns boolean
language sql security definer set search_path = public, extensions as $$
  select exists (select 1 from app_secret where k = 'pass' and v = crypt(p, v));
$$;
revoke all on function public._pass_ok(text) from public, anon, authenticated;

-- 第一次设置口令（只在还没有口令时有效）；以后改口令用 change_pass
create or replace function public.init_pass(p_new text) returns text
language plpgsql security definer set search_path = public, extensions as $$
begin
  if exists (select 1 from app_secret where k = 'pass') then return '已经设过口令，请用 change_pass'; end if;
  if length(p_new) < 6 then return '口令至少 6 位'; end if;
  insert into app_secret(k, v) values ('pass', crypt(p_new, gen_salt('bf')));
  return 'ok';
end $$;
revoke all on function public.init_pass(text) from public, anon, authenticated;   -- 只能在 SQL Editor 里跑

create or replace function public.change_pass(p_old text, p_new text) returns text
language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _pass_ok(p_old) then perform pg_sleep(1); return '口令不对'; end if;
  if length(p_new) < 6 then return '口令至少 6 位'; end if;
  update app_secret set v = crypt(p_new, gen_salt('bf')) where k = 'pass';
  return 'ok';
end $$;

-- 检查口令（网页「解锁」用）
create or replace function public.check_pass(p_pass text) returns boolean
language plpgsql security definer set search_path = public, extensions as $$
begin
  if _pass_ok(p_pass) then return true; end if;
  perform pg_sleep(1);
  return false;
end $$;

-- 新建 / 修改组合；p_id 为空 = 新建；返回组合 id
create or replace function public.save_combo(p_pass text, p_id uuid, p_name text, p_config jsonb, p_note text default '')
returns uuid language plpgsql security definer set search_path = public, extensions as $$
declare r uuid;
begin
  if not _pass_ok(p_pass) then perform pg_sleep(1); raise exception '口令不对'; end if;
  if coalesce(trim(p_name), '') = '' then raise exception '名字不能为空'; end if;
  if pg_column_size(p_config) > 200000 then raise exception '配置太大'; end if;
  if p_id is null then
    insert into combos(name, config, note) values (trim(p_name), p_config, coalesce(p_note, '')) returning id into r;
  else
    update combos set name = trim(p_name), config = p_config, note = coalesce(p_note, ''), updated_at = now()
     where id = p_id and not deleted returning id into r;
    if r is null then raise exception '组合不存在'; end if;
  end if;
  return r;
end $$;

-- 填聚宽实测成绩
create or replace function public.set_jq(p_pass text, p_id uuid, p_jq jsonb)
returns void language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _pass_ok(p_pass) then perform pg_sleep(1); raise exception '口令不对'; end if;
  update combos set jq = p_jq where id = p_id;
end $$;

-- 删除（软删除，可在 SQL Editor 里恢复）
create or replace function public.delete_combo(p_pass text, p_id uuid)
returns void language plpgsql security definer set search_path = public, extensions as $$
begin
  if not _pass_ok(p_pass) then perform pg_sleep(1); raise exception '口令不对'; end if;
  update combos set deleted = true, updated_at = now() where id = p_id;
end $$;

grant execute on function public.change_pass(text, text), public.check_pass(text),
  public.save_combo(text, uuid, text, jsonb, text), public.set_jq(text, uuid, jsonb), public.delete_combo(text, uuid) to anon, authenticated;
grant select on public.combos to anon, authenticated;
