create table if not exists webpush_messages(
	id int auto_increment,
	title varchar(255) not null,
	options text default null,
	pushed boolean not null,
	creation_time timestamp default current_timestamp,
	primary key(id)
);

create table if not exists webpush_schedules(
	id int auto_increment,
    day int not null,
	hour int NOT NULL,
    minute int NOT NULL,
	primary key(id)
);

create table if not exists webpush_requests(
	id int auto_increment,
	queue_id int not null,
	subscriber_id int not null,
	creation_time timestamp default current_timestamp,
	primary key(id)
);

create table if not exists webpush_subscribers(
	id int auto_increment,
    endpoint varchar(1000) not null,
    expiration_time timestamp default null,
    keys_p256dh varchar(255) not null,
	keys_auth varchar(50) not null,
	primary key(id)
);

ALTER TABLE webpush_subscribers
ADD INDEX idx_expiration_time (expiration_time);

create table if not exists webpush_log_messages(
	id int auto_increment,
	title varchar(255) not null,
	options text default null,
	total_subscribers int not null,
	total_pushes int not null,
	start_time timestamp default current_timestamp,
	end_time timestamp default null,
	primary key(id)
);
