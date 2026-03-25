#!/usr/bin/env python3
"""
    SEMANTIC FILE SEARCH DAEMON
"""

import asyncio
import os
import torch
import chromadb
import json
import magic
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from sentence_transformers import SentenceTransformer

PATH = os.getenv("HOME")
DB = "./sf_search_index"
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL = SentenceTransformer('all-MiniLM-L6-v2', device=DEVICE)
SOCKET_PATH = "/tmp/sfd.sock"
MAX_FILE_SIZE = 5 * 1024 * 1024 # 5MB
MIMETYPE_WHITELIST = [
    "application/xml", "application/json", "application/x-javascript", "application/x-sh", "application/x-httpd-php"
]
EXCLUDED_DIRS = [
    ".git", ".cache", "venv", "node_modules", "__pycache__", ".librewolf", "chromium", ".local"
]
STATIC_SYSTEM_FILES = [
    # Networking
    ("/etc/hosts", "System File. Static table lookup for hostnames and IP addresses. Maps local names to network addresses."),
    ("/etc/resolv.conf", "System File. DNS nameserver configuration and domain resolution settings for network queries."),
    ("/etc/hostname", "System File. The system's unique network name used to identify this machine on a network."),
    ("/etc/services", "System File. Standard mapping of network service names to their respective port numbers and protocols."),
    
    # System Identity
    ("/etc/os-release", "System File. Operating system identification data including distro name (Arch Linux), version, and ID."),
    ("/etc/issue", "System File. System identification and message text displayed before the network login prompt."),
    ("/proc/version", "System File. Live kernel information including version number, compiler info, and build date."),
    
    # Hardware & Performance (Virtual Files)
    ("/proc/cpuinfo", "System File. Detailed processor architecture, model name, core count, and CPU flags."),
    ("/proc/meminfo", "System File. Real-time statistics on system memory usage, including total RAM, free memory, and swap."),
    ("/proc/cmdline", "System File. The specific arguments and parameters passed to the Linux kernel during the boot process."),
    
    # User & Group Metadata
    ("/etc/passwd", "System File. User account database containing usernames, UIDs, and default login shells."),
    ("/etc/group", "System File. System group definitions and the list of users belonging to each group."),
    ("/etc/shells", "System File. List of valid and trusted login shells currently installed on the system (e.g., bash, zsh).")
]
SEARCHN = 50

client = chromadb.PersistentClient(path=DB)
collection = client.get_or_create_collection(name="files")

class IndexHandler(FileSystemEventHandler):
    def __init__(self, loop, queue):
        self.loop = loop
        self.queue = queue

    def is_excluded(self, path):
        parts = path.split(os.sep)
        return any(part in EXCLUDED_DIRS for part in parts)

    def on_deleted(self, event):
        if not event.is_directory and not self.is_excluded(event.src_path):
            self.loop.call_soon_threadsafe(self.queue.put_nowait, ("DELETE", event.src_path))

    def on_created(self, event):
        if not event.is_directory and not self.is_excluded(event.src_path):
            self.loop.call_soon_threadsafe(self.queue.put_nowait, ("INDEX", event.src_path))

    def on_moved(self, event):
        self.on_deleted(event)

        if not event.is_directory and not self.is_excluded(event.dest_path):
            self.loop.call_soon_threadsafe(self.queue.put_nowait, ("INDEX", event.dest_path))

mime_detector = magic.Magic(mime=True)
def is_allowed_type(filepath):
    if os.path.basename(filepath).startswith("."):
        return False
    
    try:
        mime = mime_detector.from_file(filepath)
        return mime.startswith("text/") or mime in MIMETYPE_WHITELIST
    except:
        return False

def get_chunks(text, window_size=500, overlap=100):
    if len(text) <= window_size:
        return [text]

    chunks = []
    start = 0

    while start < len(text):
        end = start + window_size
        chunk = text[start:end]
        chunks.append(chunk)

        start += window_size - overlap

    return chunks

async def index_file(filepath, model, collection, batch_size=5000, header_desc="This file is part of the project."):
    with open(filepath, 'r', encoding="utf-8", errors="ignore") as f:
        text = f.read()

    chunks = get_chunks(text)
    if not chunks:
        return

    filename=  os.path.basename(filepath)
    parent_dir = os.path.dirname(filepath)
    name_ctx = f"File name: {filename}. Location: {parent_dir}. {header_desc}"

    collection.add(
        embeddings=[model.encode(name_ctx).tolist()],
        documents=[name_ctx],
        metadatas=[{
            "path": filepath,
            "chunk": "header"
        }],
        ids=[f"{filepath}_header"]
    )

    for i in range(0, len(chunks), batch_size):
        batch_chunks = chunks[i:i+batch_size]
        batch_embeddings = model.encode(batch_chunks).tolist()
        batch_ids = [f"{filepath}_{j}" for j in range(i, i+len(batch_chunks))]
        batch_meta = [{"path": filepath, "chunk": j} for j in range(i, i+len(batch_chunks))]

        collection.add(
            embeddings=batch_embeddings,
            documents=batch_chunks,
            metadatas=batch_meta,
            ids=batch_ids
        )

async def file_indexer_worker(queue, model, collection, lock):
    """Background task to process queue"""
    while True:
        action, filepath = await queue.get()
        
        async with lock:
            if action == "DELETE":
                collection.delete(where={"path": filepath})

            elif action == "INDEX":
                collection.delete(where={"path": filepath})

                if is_allowed_type(filepath):
                    await index_file(filepath, model, collection)

        queue.task_done()

db_lock = asyncio.Lock()
async def handle_client(reader, writer):
    raw_data = await reader.read(4096)
    data = json.loads(raw_data.decode())

    action = data.get("action")
    text = data.get("text")

    async with db_lock:
        vector = MODEL.encode(text).tolist()

        if action == "search":
            results = collection.query(query_embeddings=[vector], n_results=SEARCHN)
            response = json.dumps(results)
        elif action == "index":
            collection.add(embeddings=[vector], documents=[text], ids=[data["id"]])
            response = "Success"

    writer.write(response.encode())
    await writer.drain()
    writer.close()

async def main():
    if os.path.exists(SOCKET_PATH):
        os.remove(SOCKET_PATH)

    queue = asyncio.Queue()
    lock = asyncio.Lock()

    observer = Observer()
    observer.schedule(IndexHandler(asyncio.get_event_loop(), queue), path="/home/caden/", recursive=True)
    observer.start()

    print("Starting daemon...")

    # add interesting sytsem files to the DB
    for path, desc in STATIC_SYSTEM_FILES:
        collection.delete(where={"path": path})
        await index_file(path, MODEL, collection, header_desc=desc)

    await asyncio.gather(
        file_indexer_worker(queue, MODEL, collection, lock),
        asyncio.start_unix_server(handle_client, path=SOCKET_PATH)
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
