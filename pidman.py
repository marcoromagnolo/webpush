import atexit
import os

# Function to remove the PID file when the program exits
def remove_pid_file(pid_file_path):
    try:
        os.remove(pid_file_path)
    except OSError as e:
        print(f"Error removing PID file: {e}")

# Write the PID to the file
def add_pid_file(pid_file_path):
    pid = os.getpid()
    with open(pid_file_path, "w") as pid_file:
        pid_file.write(str(pid))

    # Register the exit function
    atexit.register(remove_pid_file, pid_file_path)    
