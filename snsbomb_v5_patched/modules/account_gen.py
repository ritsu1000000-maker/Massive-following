"""
account_gen.py
Random identity generator for account creation.
Uses Faker — no external API calls.
"""
import random
import string
from faker import Faker

fake = Faker()


def generate_identity() -> dict:
    """
    Returns a full fake identity dict:
    {name, username, email, password, dob, bio}
    """
    first = fake.first_name()
    last = fake.last_name()
    username = _make_username(first, last)
    password = _make_password()
    dob = fake.date_of_birth(minimum_age=18, maximum_age=35).strftime("%Y-%m-%d")

    return {
        "full_name": f"{first} {last}",
        "first_name": first,
        "last_name": last,
        "username": username,
        "password": password,
        "dob": dob,
        "bio": fake.sentence(nb_words=8),
    }


def _make_username(first: str, last: str) -> str:
    """
    Combine first/last with a random suffix to hit a fresh username.
    Pattern: firstname_last123  or  last.first_99
    """
    suffix = "".join(random.choices(string.digits, k=random.randint(2, 4)))
    sep = random.choice(["_", ".", ""])
    parts = [first.lower(), last.lower()]
    random.shuffle(parts)
    return f"{parts[0]}{sep}{parts[1]}{suffix}"


def _make_password() -> str:
    """16-char password: upper + lower + digit + symbol."""
    chars = (
        random.choices(string.ascii_uppercase, k=3)
        + random.choices(string.ascii_lowercase, k=6)
        + random.choices(string.digits, k=4)
        + random.choices("!@#$%^&*", k=3)
    )
    random.shuffle(chars)
    return "".join(chars)
