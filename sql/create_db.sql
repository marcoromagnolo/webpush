CREATE DATABASE webpush
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
CREATE USER 'webpush'@'localhost' IDENTIFIED BY 'password';
GRANT ALL PRIVILEGES ON webpush.* TO 'webpush'@'localhost';
FLUSH PRIVILEGES;