-- psql "$DATABASE_URL" -v ro_password="'<mật khẩu role>'" -f api/db/readonly_role.sql
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'riverbank_ro') then
        execute format('create role riverbank_ro login password %L', :ro_password);
    end if;
end $$;
grant connect on database riverbank to riverbank_ro;
grant usage on schema public to riverbank_ro;
grant select on all tables in schema public to riverbank_ro;
alter default privileges in schema public grant select on tables to riverbank_ro;
