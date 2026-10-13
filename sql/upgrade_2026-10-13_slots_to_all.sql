-- Every slot of the schedule sends the next message of the queue to ALL the active subscribers: the split of the
-- subscribers in shares (partitions) and the setting "messages per subscriber a day" are removed. The queue is fed
-- with few messages a day (e.g. the top news of the site) and the schedule has the few best times of the day.
--
--     mariadb webpush < sql/upgrade_2026-10-13_slots_to_all.sql
--
-- A site with the old default schedule (every day, every hour 8:00-22:00: 105 slots on the hour) or without slots
-- gets the new default: 07:30 12:30 15:30 18:30 21:00 every day (the schedule is always there, changed in the
-- console, page Schedules). Any other schedule is kept: check it (now every slot is a notification for every
-- subscriber). It can be run again.

ALTER TABLE site DROP COLUMN IF EXISTS messages_per_day;
ALTER TABLE delivery DROP COLUMN IF EXISTS partition_index, DROP COLUMN IF EXISTS partitions;

CREATE TEMPORARY TABLE old_default AS
SELECT s.id AS site_id FROM site s LEFT JOIN schedule_slot l ON l.site_id = s.id
GROUP BY s.id
HAVING COUNT(l.id) = 0 OR (COUNT(l.id) = 105 AND SUM(l.minute = 0 AND l.hour BETWEEN 8 AND 22) = 105);

DELETE FROM schedule_slot WHERE site_id IN (SELECT site_id FROM old_default);

INSERT INTO schedule_slot (site_id, day, hour, minute)
SELECT o.site_id, d.day, t.hour, t.minute
FROM old_default o
CROSS JOIN (SELECT 1 AS day UNION SELECT 2 UNION SELECT 3 UNION SELECT 4 UNION SELECT 5 UNION SELECT 6 UNION SELECT 7) d
CROSS JOIN (SELECT 7 AS hour, 30 AS minute UNION SELECT 12, 30 UNION SELECT 15, 30 UNION SELECT 18, 30 UNION SELECT 21, 0) t;

-- Check: the slots a week of every site (35 = the new default)
SELECT s.code, COUNT(l.id) AS slots_a_week, s.id IN (SELECT site_id FROM old_default) AS changed
FROM site s LEFT JOIN schedule_slot l ON l.site_id = s.id
GROUP BY s.id;

DROP TEMPORARY TABLE old_default;
