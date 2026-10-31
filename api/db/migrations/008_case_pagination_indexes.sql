create index cases_created_cursor on cases (created_at, case_id);
create index cases_seller_cursor on cases (seller_id, created_at, case_id);
