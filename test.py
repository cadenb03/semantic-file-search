from sentence_transformers import SentenceTransformer

model = SentenceTransformer("all-MiniLM-L6-v2")

text_chunks = ["The Linux kernel is open source.", "Tux is the official mascot."]
embeddings = model.encode(text_chunks)
