"""Small helpers shared by the bot modules."""
import math


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi
