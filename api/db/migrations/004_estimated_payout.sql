do $$
declare
    name text;
begin
    select conname into name from pg_constraint
    where conrelid = 'seller_feature_snapshots'::regclass
      and pg_get_constraintdef(oid) like '%payout_origin%';
    execute format('alter table seller_feature_snapshots drop constraint %I', name);
end $$;
alter table seller_feature_snapshots add constraint seller_features_payout_origin
    check (payload->>'payout_origin' in ('observed', 'estimated', 'simulated'));
