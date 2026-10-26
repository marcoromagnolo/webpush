-- Sending at the time of the messages: site.use_schedule. true (default, as before): the slots of the schedule send
-- the queue; false: every message is sent at its send_after (REST API or console), a message without it at once.
-- Changed in the console, Sites > the site > Sending.
--
--     mariadb webpush < sql/upgrade_2026-10-26_site_use_schedule.sql
--
-- It can be run again.

ALTER TABLE site ADD COLUMN IF NOT EXISTS use_schedule boolean not null default true AFTER default_lang;

-- Check: the sending of every site (1 = schedule)
SELECT code, use_schedule FROM site;
