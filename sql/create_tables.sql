-- Tables of webpush (README.md > Install).

-- Websites that send notifications: one installation serves all of them
create table if not exists site(
	id int auto_increment,
	code varchar(50) not null,  -- slug in the API urls (/api/v1/sites/<code>/...)
	name varchar(100) not null,
	url varchar(255) not null,  -- home page; its origin is always allowed by CORS
	allowed_origins text null default null,  -- other origins allowed by CORS on the public API, one per line
	enabled boolean not null default true,  -- disabled: no scheduled sending, the public API answers 404
	vapid_private_key varchar(100) null default null,  -- base64url, raw 32 bytes
	vapid_public_key varchar(120) null default null,  -- base64url, uncompressed point (applicationServerKey)
	vapid_sub varchar(255) not null,  -- contact of the VAPID claims: mailto:... or https://...
	ttl_seconds int not null default 86400,  -- the push service keeps a notification this long for an offline browser
	default_icon varchar(1000) null default null,
	default_badge varchar(1000) null default null,
	default_lang varchar(35) null default null,  -- lang of the notifications without one (BCP 47, e.g. it-IT)
	created_at timestamp not null default current_timestamp,
	primary key(id),
	unique key unique_site_code (code)
);

-- Browsers subscribed to the notifications of a site. A removed subscriber is kept (removed_at) for the
-- statistics: it is active again if the browser subscribes again
create table if not exists subscriber(
	id int auto_increment,
	site_id int not null,
	endpoint varchar(1000) not null,
	endpoint_hash char(64) not null,  -- sha256 of the endpoint: unique per site
	keys_p256dh varchar(255) not null,
	keys_auth varchar(100) not null,
	expiration_time bigint null default null,  -- epoch milliseconds (expirationTime of the browser)
	user_agent varchar(255) null default null,
	is_test boolean not null default false,  -- browser of an administrator (console): only the test sendings
	created_at timestamp not null default current_timestamp,
	updated_at datetime null default null,
	last_success_at datetime null default null,
	failures int not null default 0,  -- consecutive failed sendings
	last_error varchar(500) null default null,  -- answer of the push service to the last failed sending
	removed_at datetime null default null,
	removed_reason varchar(20) null default null,  -- gone | failures | expired | unsubscribed | manual
	primary key(id),
	unique key unique_site_endpoint (site_id, endpoint_hash),
	key idx_subscriber_site_created (site_id, created_at),
	key idx_subscriber_site_removed (site_id, removed_at),
	constraint fk_subscriber_site foreign key (site_id) references site(id) on delete cascade
);

-- Notifications: queued by the API or the console, sent one per schedule slot (oldest first, sort_order)
create table if not exists message(
	id int auto_increment,
	site_id int not null,
	title varchar(255) not null,
	options text null default null,  -- JSON: body, icon, image, data.url, actions...
	status varchar(20) not null default 'queued',  -- queued | sending | sent | failed | cancelled
	sort_order bigint not null default 0,  -- position in the queue
	send_after datetime null default null,  -- not sent before
	created_at timestamp not null default current_timestamp,
	created_by varchar(100) null default null,  -- user of the console or API token
	started_at datetime null default null,
	sent_at datetime null default null,
	primary key(id),
	key idx_message_queue (site_id, status, sort_order),
	constraint fk_message_site foreign key (site_id) references site(id) on delete cascade
);

-- Weekly slots of a site: at each slot the next queued message is sent to a share of the subscribers
create table if not exists schedule_slot(
	id int auto_increment,
	site_id int not null,
	day tinyint not null,  -- 1 Monday ... 7 Sunday
	hour tinyint not null,
	minute tinyint not null,
	primary key(id),
	unique key unique_slot (site_id, day, hour, minute),
	constraint fk_slot_site foreign key (site_id) references site(id) on delete cascade
);

-- A sending of a message to the active subscribers of a site (slot, by hand) or to its test browsers
create table if not exists delivery(
	id int auto_increment,
	site_id int not null,
	message_id int null default null,
	kind varchar(10) not null,  -- slot | now | test
	slot char(5) null default null,  -- HH:MM of the slot
	total int not null default 0,
	success int not null default 0,
	gone int not null default 0,  -- subscribers removed: 404 / 410
	errors int not null default 0,
	start_time datetime not null,
	end_time datetime null default null,
	primary key(id),
	key idx_delivery_site_start (site_id, start_time),
	key idx_delivery_message (message_id),
	constraint fk_delivery_site foreign key (site_id) references site(id) on delete cascade
);

-- Clicks on the notifications (sent by the service worker)
create table if not exists click(
	id bigint auto_increment,
	site_id int not null,
	message_id int null default null,
	created_at timestamp not null default current_timestamp,
	primary key(id),
	key idx_click_site_created (site_id, created_at),
	key idx_click_message (message_id),
	constraint fk_click_site foreign key (site_id) references site(id) on delete cascade
);

-- Administrators of the console
create table if not exists user(
	id int auto_increment,
	username varchar(255) not null,
	hash varchar(255) not null,
	primary key(id),
	unique key unique_username (username)
);

-- Tokens of the API clients (e.g. the WordPress plugin that queues the messages)
create table if not exists api_token(
	id int auto_increment,
	name varchar(100) not null,  -- client
	site_id int not null,  -- a token works for its site only
	token_hash char(64) not null,
	token_prefix varchar(16) not null,  -- first characters, to recognize the token
	created_at timestamp not null default current_timestamp,
	created_by int null default null,  -- user id
	expires_at datetime null default null,
	revoked_at datetime null default null,
	last_used_at datetime null default null,
	last_used_ip varchar(45) null default null,
	primary key(id),
	unique key unique_token_hash (token_hash),
	constraint fk_token_site foreign key (site_id) references site(id) on delete cascade
);

create table if not exists ip_blacklist(
	remote_address varchar(45) not null,  -- IPv4 or IPv6
	request_time timestamp default current_timestamp,  -- last failed request
	number_of_requests int not null,  -- failed requests
	blocked boolean default false,  -- permanent block
	note varchar(255) null default null,
	primary key(remote_address)
);

create table if not exists error_log(
	id int auto_increment,
	created_at timestamp not null default current_timestamp,
	phase varchar(20) not null,  -- push | scheduler | api | app
	source varchar(100) not null,  -- logger
	site_id int null default null,
	message_id int null default null,
	url varchar(2000) null default null,
	message text not null,
	traceback mediumtext null default null,
	resolved boolean not null default false,
	primary key(id),
	key idx_error_log_open (resolved, phase),
	key idx_error_log_created (created_at)
);
