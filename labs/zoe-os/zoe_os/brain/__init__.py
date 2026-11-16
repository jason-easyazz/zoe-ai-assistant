"""The brain's side of Zoe OS: the brain proposes, the kernel disposes.

``bridge`` is what the brain process holds (a socket path and a credential, nothing else).
``drivers`` is what the KERNEL process holds (the stores the actions write to). The two never
share a process in a real run; the tests put them in separate processes to show it.
"""
