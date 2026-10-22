-- psql "$DATABASE_URL" -v dev_password="'<mật khẩu>'" -f api/db/dev_role.sql
-- Role đọc, query, sửa data cho cả team. Không tạo/xóa bảng, không đổi schema.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'riverbank_dev') then
        execute format('create role riverbank_dev login password %L', :dev_password);
    end if;
end $$;
grant connect on database riverbank to riverbank_dev;
grant usage on schema public to riverbank_dev;
grant select, insert, update, delete on all tables in schema public to riverbank_dev;
grant usage, select on all sequences in schema public to riverbank_dev;
alter default privileges in schema public grant select, insert, update, delete on tables to riverbank_dev;
alter default privileges in schema public grant usage, select on sequences to riverbank_dev;
