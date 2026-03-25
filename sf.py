#!/usr/bin/env python3
import socket
import json
import argparse
import curses
import os
import time
import subprocess
import sys

os.environ.setdefault('ESCDELAY', '25')

def search_query(query):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect("/tmp/sfd.sock")
        q = json.dumps({
                           "action": "search",
                           "text": query
                       })
        s.sendall(q.encode())
        response = s.recv(4096 * 8)
        return json.loads(response.decode())

def get_results(query):
    query_vector = search_query(query)
    
    files = []
    for i, f in enumerate(query_vector["metadatas"][0]):
        if f["path"] not in files:
            files.append(f["path"])

    return files

def main(stdscr):
    parser = argparse.ArgumentParser(
        prog="sf",
        description="semantic file search"
    )

    parser.add_argument("search", nargs='*')
    args = parser.parse_args()
    query = " ".join(args.search)
    files = get_results(query)

    curses.use_default_colors()
    curses.curs_set(0)
    stdscr.timeout(100)
    curses.init_pair(1, -1, curses.COLOR_BLUE)
    curses.init_pair(2, curses.COLOR_WHITE, -1)
    curses.init_pair(3, -1, curses.COLOR_BLACK)
    # stdscr.nodelay(0)

    current_row = 0
    search_text = query
    needs_search = False
    last_time = time.time()
    delay = 0.5

    while True:
        stdscr.clear()
        h, w = stdscr.getmaxyx()

        header = f" Search: {search_text}_"
        stdscr.attron(curses.color_pair(3))
        stdscr.addstr(0, 0, header.ljust(w))
        stdscr.attroff(curses.color_pair(3))

        for i, f in enumerate(files):
            x = 0
            y = 2 + i
            if i == current_row:
                stdscr.attron(curses.color_pair(1))
                stdscr.addstr(y, x, f"> {f}")
                stdscr.attroff(curses.color_pair(1))
            else:
                if i % 2 == 0:
                    stdscr.attron(curses.color_pair(2))
                stdscr.addstr(y, x, f"  {f}")
                stdscr.attroff(curses.color_pair(2))

        stdscr.refresh()
        key = stdscr.getch()

        if key == curses.KEY_UP:
            if current_row > 0:
                current_row -= 1
            else:
                current_row = len(files) - 1
            
        elif key == curses.KEY_DOWN:
            if current_row < len(files) - 1:
                current_row += 1
            else:
                current_row = 0
            
        elif key == curses.KEY_ENTER or key in [10, 13]:
            # Handle selection
            # You can add logic here for what happens on Enter
            return files[current_row]

        elif key == curses.KEY_RIGHT:
            cddir = os.path.dirname(files[current_row])
            subprocess.run([
                "kitten", "@", "send-text",
                "--match", "state:focused",
                f"cd {cddir}\n"
            ])
            return

        elif key == curses.KEY_BACKSPACE or key == 127: # backspace
            search_text = search_text[:-1]
            needs_search = True
            last_time = time.time()
            
        elif key == 23: # ctrl+backspace
            stripped = search_text.rstrip()
            last_space_i = stripped.rfind(' ')

            if last_space_i == -1:
                search_text = ''
            else:
                search_text = stripped[:last_space_i + 1]

            needs_search = True
            last_time = time.time()

        elif 32 <= key <= 126:  # Regular printable characters
            search_text += chr(key)
            needs_search = True
            last_time = time.time()
            
        elif key == 27:  # ESC key to exit
            break
            
        elif key == -1:
            if needs_search and (time.time() - last_time) >= delay:
                files = get_results(search_text)

                if current_row >= len(files):
                    current_row = len(files) - 1

if __name__ == "__main__":
    file_to_open = curses.wrapper(main)

    if file_to_open:
        subprocess.run([os.getenv("EDITOR"), file_to_open])
