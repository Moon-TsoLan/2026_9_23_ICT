"""Attachment screening and page parsing layer.

census  content-addressed inventory (trust the bytes, not the extension)
peek    cheap text view for the gate; never raises
screen  name rules, then a text gate, then an image gate for scans
client  HTTP page-parsing service; results stay in memory
"""
