-- Every API token belongs to one site: api_token.site_id becomes required (the first versions allowed tokens of
-- every site, site_id null, no longer accepted by the API since this version).
--
--     mariadb webpush < sql/upgrade_2026-10-13_token_site.sql
--
-- The tokens without a site: with a single site in the installation they become tokens of that site (the clients
-- keep working); with more sites they are deleted (they are listed first: create a token for each site of their
-- client, page API tokens). It can be run again.

SELECT id, name, token_prefix, created_at, revoked_at FROM api_token WHERE site_id IS NULL;

UPDATE api_token SET site_id = (SELECT MIN(id) FROM site)
WHERE site_id IS NULL AND (SELECT COUNT(*) FROM site) = 1;

DELETE FROM api_token WHERE site_id IS NULL;

ALTER TABLE api_token MODIFY site_id int not null;

-- Check: the tokens of every site
SELECT s.code, COUNT(t.id) AS tokens, SUM(t.revoked_at IS NULL) AS not_revoked
FROM site s LEFT JOIN api_token t ON t.site_id = s.id
GROUP BY s.id;
