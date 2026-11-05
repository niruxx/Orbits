"""Dispatch the headless Shell provider before importing GTK."""
import sys


def main():
    if '--search-provider' in sys.argv:
        from orbits.search_provider import main as provider_main
        return provider_main()
    from orbits.application import main as application_main
    return application_main()
