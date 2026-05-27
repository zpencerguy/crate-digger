from django.db import migrations


def add_column_if_missing(apps, schema_editor, table: str, column: str, definition: str) -> None:
    with schema_editor.connection.cursor() as cursor:
        columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def add_soundcloud_fields(apps, schema_editor) -> None:
    add_column_if_missing(apps, schema_editor, "mixtapes", "artwork_url", "TEXT")
    add_column_if_missing(apps, schema_editor, "mixtapes", "soundcloud_embed_html", "TEXT")


class Migration(migrations.Migration):
    dependencies = []

    operations = [
        migrations.RunPython(add_soundcloud_fields, migrations.RunPython.noop),
    ]
