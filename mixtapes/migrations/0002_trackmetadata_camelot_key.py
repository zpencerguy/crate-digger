from django.db import migrations


def add_camelot_key(apps, schema_editor) -> None:
    with schema_editor.connection.cursor() as cursor:
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(track_metadata)")}
        if "camelot_key" not in columns:
            cursor.execute("ALTER TABLE track_metadata ADD COLUMN camelot_key TEXT")


class Migration(migrations.Migration):
    dependencies = [
        ("mixtapes", "0001_soundcloud_display_fields"),
    ]

    operations = [
        migrations.RunPython(add_camelot_key, migrations.RunPython.noop),
    ]
