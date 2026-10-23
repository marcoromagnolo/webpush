-- Language of the notifications of a site: site.default_lang (BCP 47, e.g. it-IT) is the option lang of the
-- messages without one, like the default icon and badge. Set it in the console, Sites > the site > Language.
--
--     mariadb webpush < sql/upgrade_2026-10-23_site_lang.sql
--
-- It can be run again.

ALTER TABLE site ADD COLUMN IF NOT EXISTS default_lang varchar(35) null default null AFTER default_badge;

-- Check: the language of every site (NULL until set in the console)
SELECT code, default_lang FROM site;
