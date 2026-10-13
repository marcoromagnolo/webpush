-- Upgrade of a database of the single site version (tables messages, subscribers, schedules, log_messages,
-- requests) to the multi site version. Run it once, after sql/create_tables.sql, on a backup first
-- (README.md > Upgrade from the single site version):
--
--     mariadb webpush < sql/create_tables.sql
--     mariadb webpush < sql/upgrade_2026-10-12_multisite.sql
--
-- The site of the old data is created with the values below: change them before running the script.
-- The VAPID keys of the site are imported afterwards (console > Sites > the site > Keys > Import, or
-- docker exec webpush python vapid.py import <code> ...): the browsers already subscribed keep working.
-- The old tables are renamed *_old: drop them when everything works.

SET @code = 'mynews';
SET @name = 'My News';
SET @url = 'https://www.example.com';
SET @vapid_sub = 'mailto:mail@example.com';

START TRANSACTION;

INSERT INTO site (code, name, url, vapid_sub) VALUES (@code, @name, @url, @vapid_sub);
SET @site_id = LAST_INSERT_ID();

-- Subscribers: the same ids; a duplicated endpoint keeps its most recent row (newest keys);
-- expiration_time from timestamp to epoch milliseconds
INSERT IGNORE INTO subscriber (id, site_id, endpoint, endpoint_hash, keys_p256dh, keys_auth, expiration_time)
SELECT id, @site_id, endpoint, SHA2(endpoint, 256), keys_p256dh, keys_auth,
       IF(expiration_time IS NULL, NULL, UNIX_TIMESTAMP(expiration_time) * 1000)
FROM subscribers
ORDER BY id DESC;

-- Messages: pushed -> sent, the others stay in the queue in the same order
INSERT INTO message (id, site_id, title, options, status, sort_order, created_at, created_by)
SELECT id, @site_id, title, options, IF(pushed, 'sent', 'queued'), id, creation_time, 'migration'
FROM messages;

-- Schedule: the 5 default times every day (each slot sends to all the subscribers), not the old slots: they were
-- made for a share of the subscribers each (change them in the console, page Schedules)
INSERT IGNORE INTO schedule_slot (site_id, day, hour, minute)
SELECT @site_id, d.day, t.hour, t.minute
FROM (SELECT 1 AS day UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 UNION SELECT 7) d
CROSS JOIN (SELECT 7 AS hour, 30 AS minute UNION SELECT 12, 30 UNION SELECT 15, 30 UNION SELECT 18, 30 UNION SELECT 21, 0) t;

-- Sending log: no link to the message in the old table
INSERT INTO delivery (site_id, message_id, kind, total, success, start_time, end_time)
SELECT @site_id, NULL, 'slot', total_subscribers, total_pushes, start_time, end_time
FROM log_messages;

COMMIT;

RENAME TABLE messages TO messages_old,
             subscribers TO subscribers_old,
             schedules TO schedules_old,
             log_messages TO log_messages_old,
             requests TO requests_old;

-- Check: the counts of the old and the new tables (subscribers: fewer if there were duplicates)
SELECT (SELECT COUNT(*) FROM subscribers_old) AS subscribers_old, (SELECT COUNT(*) FROM subscriber) AS subscriber,
       (SELECT COUNT(*) FROM messages_old) AS messages_old, (SELECT COUNT(*) FROM message) AS message,
       (SELECT COUNT(*) FROM schedules_old) AS schedules_old, (SELECT COUNT(*) FROM schedule_slot) AS schedule_slot;
