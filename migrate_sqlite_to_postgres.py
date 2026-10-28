#!/usr/bin/env python3
"""One-time migration of an existing IACC SQLite DB to an empty PostgreSQL database.

Run locally on the computer that has iacc_waybills.db. The SQLite source is opened
read-only. DATABASE_URL must be set privately in the shell environment.
"""
import os
import sys
import argparse
import sqlite3
from sqlalchemy import create_engine, MetaData, Table, select, text

ORDER = ['user', 'customer', 'driver', 'waybill', 'audit']

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sqlite_file', nargs='?', default='iacc_waybills.db', help='Path to local SQLite database')
    args = parser.parse_args()
    source_path = os.path.abspath(args.sqlite_file)
    database_url = os.environ.get('DATABASE_URL', '').strip()
    if not os.path.isfile(source_path):
        sys.exit(f'ERROR: SQLite database not found: {source_path}')
    if not database_url.startswith(('postgresql://', 'postgres://', 'postgresql+psycopg2://')):
        sys.exit('ERROR: Set DATABASE_URL to your PostgreSQL connection string in your local shell; do not put it in this script.')
    if database_url.startswith('postgres://'):
        database_url = 'postgresql://' + database_url[len('postgres://'):]

    # Read SQLite without modifying it.
    source = sqlite3.connect(f'file:{source_path}?mode=ro', uri=True)
    source.row_factory = sqlite3.Row
    src_tables = {r['name'] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    expected = set(ORDER)
    missing = expected - src_tables
    if missing:
        sys.exit('ERROR: Source database is missing expected tables: ' + ', '.join(sorted(missing)))

    target_engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with target_engine.begin() as conn:
            # Require an empty target to prevent accidental overwrites or duplicate/conflicting records.
            target_meta = MetaData()
            target_meta.reflect(bind=conn)
            nonempty = []
            for name in ORDER:
                if name in target_meta.tables and conn.execute(select(target_meta.tables[name].c.id).limit(1)).first():
                    nonempty.append(name)
            if nonempty:
                sys.exit('ERROR: Target database is not empty (rows found in: ' + ', '.join(nonempty) + '). Use a new empty database; no rows were copied.')

        # Create tables from the application's current models, but skip startup seeding.
        os.environ['DATABASE_URL'] = database_url
        os.environ['IACC_SKIP_INIT_DB'] = '1'
        from app import app, db
        with app.app_context():
            db.create_all()

        target_meta = MetaData()
        target_meta.reflect(bind=target_engine)
        counts = {}
        with target_engine.begin() as conn:
            for name in ORDER:
                src_rows = source.execute(f'SELECT * FROM "{name}"').fetchall()
                dst = target_meta.tables.get(name)
                if dst is None:
                    raise RuntimeError(f'Target table {name} was not created by the application models.')
                src_cols = set(src_rows[0].keys()) if src_rows else {r[1] for r in source.execute(f'PRAGMA table_info("{name}")')}
                common = [c.name for c in dst.columns if c.name in src_cols]
                inserted = 0
                for row in src_rows:
                    payload = {col: row[col] for col in common}
                    conn.execute(dst.insert().values(**payload))
                    inserted += 1
                counts[name] = inserted
            # Restore PostgreSQL serial sequences to the maximum preserved IDs.
            for name in ORDER:
                if 'id' in target_meta.tables[name].c:
                    conn.execute(text("SELECT setval(pg_get_serial_sequence(:tbl, 'id'), COALESCE((SELECT MAX(id) FROM \"" + name + "\"), 1), (SELECT MAX(id) IS NOT NULL FROM \"" + name + "\"))"), {'tbl': name})
        print('Migration finished. Rows copied:')
        for name, count in counts.items():
            print(f'  {name}: {count}')
        print('Next: verify these counts against the source and test login, waybill search/edit, PDF output, and audit history before switching users.')
    finally:
        source.close()
        target_engine.dispose()

if __name__ == '__main__':
    main()
