-- Delay of the sending at the time of the messages: site.send_delay_minutes. A site without schedule
-- (use_schedule false) sends every message these minutes after its send_after (or after its arrival when it has
-- none). Changed in the console, Sites > the site > Sending > Delay.
--
--     mariadb webpush < sql/upgrade_2026-10-27_site_send_delay.sql
--
-- It can be run again.

ALTER TABLE site ADD COLUMN IF NOT EXISTS send_delay_minutes int not null default 0 AFTER use_schedule;

-- Check: the sending of every site (use_schedule 1 = schedule, the delay counts with 0)
SELECT code, use_schedule, send_delay_minutes FROM site;
