DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'unlock')
       AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'benefitlens') THEN
        ALTER SCHEMA unlock RENAME TO benefitlens;
    ELSIF EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'unlock')
       AND EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'benefitlens') THEN
        RAISE EXCEPTION 'Both unlock and benefitlens schemas exist; merge them before migration';
    END IF;
END $$;
