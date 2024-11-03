import mysql.connector
from settings import DB_SETTINGS

def open_connection():
    db_connection = mysql.connector.connect(**DB_SETTINGS)
    return db_connection

def get_last_message():
    db_connection = open_connection()
    db_cursor = db_connection.cursor()
    select_query = ("SELECT id, title, options, pushed, creation_time FROM webpush_messages"
    " WHERE pushed = false"
    " ORDER BY creation_time ASC"
    " LIMIT 1")
    
    try:
        db_cursor.execute(select_query)
        row = db_cursor.fetchone()
        if row:
            id, title, options, pushed, creation_time = row
            return {
                'id': id,
                'title': title,
                'options': options,
                'pushed': pushed,
                'creation_time': creation_time
            }
    finally:
        db_cursor.close()
        db_connection.close()

def add_message(title, options):
    db_connection = open_connection()
    db_cursor = db_connection.cursor()

    insert_query = ("INSERT INTO webpush_messages (title, options, pushed) VALUES (%s, %s, %s)")
    data = (title, options, 0)

    try:
        db_cursor.execute(insert_query, data)
        db_connection.commit()
    except mysql.connector.Error as err:
        db_connection.rollback()
        raise err
    finally:
        db_cursor.close()
        db_connection.close()

def set_message_pushed(id):
    db_connection = open_connection()
    db_cursor = db_connection.cursor()

    insert_query = ("UPDATE webpush_messages set pushed=true WHERE id=%s")
    data = (id, )

    try:
        db_cursor.execute(insert_query, data)
        db_connection.commit()
    except mysql.connector.Error as err:
        db_connection.rollback()
        raise err
    finally:
        db_cursor.close()
        db_connection.close()

def get_subscribers(expired=False):
    db_connection = open_connection()
    db_cursor = db_connection.cursor()
    select_query = "SELECT id, endpoint, expiration_time, keys_p256dh, keys_auth FROM webpush_subscribers"
    if expired:
        select_query += " WHERE expiration_time >= NOW()"
    
    try:
        db_cursor.execute(select_query)
        queue = []
        for row in db_cursor.fetchall():
            id, endpoint, expiration_time, keys_p256dh, keys_auth = row
            queue.append({
                'id': id,
                'endpoint': endpoint,
                'expiration_time': expiration_time,
                'keys_p256dh': keys_p256dh,
                'keys_auth': keys_auth
            })
        return queue
    finally:
        db_cursor.close()
        db_connection.close()

def add_subscriber(token):
    db_connection = open_connection()
    db_cursor = db_connection.cursor()

    insert_query = ("INSERT INTO webpush_subscribers (endpoint, expiration_time, keys_p256dh, keys_auth) VALUES (%s, %s, %s, %s)")
    data = (token["endpoint"], token["expiration_time"], token["keys_p256dh"], token["keys_auth"])

    try:
        db_cursor.execute(insert_query, data)
        db_connection.commit()
    except mysql.connector.Error as err:
        db_connection.rollback()
        raise err
    finally:
        db_cursor.close()
        db_connection.close()

def remove_subscriber(subscriber):
    db_connection = open_connection()
    db_cursor = db_connection.cursor()

    delete_query = ("DELETE FROM webpush_subscribers WHERE id=%s")
    data = (subscriber["id"], )

    try:
        db_cursor.execute(delete_query, data)
        db_connection.commit()
    except mysql.connector.Error as err:
        db_connection.rollback()
        raise err
    finally:
        db_cursor.close()
        db_connection.close()        

def add_log_message(log):
    db_connection = open_connection()
    db_cursor = db_connection.cursor()

    insert_query = ("INSERT INTO webpush_log_messages (title, options, total_subscribers, total_pushes, start_time, end_time) VALUES (%s, %s, %s, %s, %s, %s)")
    data = (log["title"], log["options"], log["total_subscribers"], log["total_pushes"], log["start_time"], log["end_time"])

    try:
        db_cursor.execute(insert_query, data)
        db_connection.commit()
    except mysql.connector.Error as err:
        db_connection.rollback()
        raise err
    finally:
        db_cursor.close()
        db_connection.close()                                 
