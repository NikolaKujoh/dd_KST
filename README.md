# DD

Interna aplikacija za subotnju hranu. Flask + SQLite.

- Registracija nadimkom i lozinkom. Prvi registrovani nalog je admin.
- Admin odobrava naloge i upisuje display name, unosi jelo za subotu i priloge.
- Porudžbine: subota 17:00–22:30 (Europe/Belgrade).
- Red: nikad nije jeo, pa jeo jednom, pa dodatne porudžbine. Ko je jeo 2 puta, blokiran je dok ga admin ne odblokira.
- Admin: `/admin` (red, čekiranje, zbir priloga), `/admin/izvestaj` (nisu jeli / jeli jednom / blokirani).

## cPanel

1. **Setup Python App**: Python 3.9+, app root npr. `dd`, startup file `passenger_wsgi.py`, entry point `application`.
2. Otpremi fajlove u app root (Git Version Control ili File Manager).
3. U Python App ekranu dodaj `requirements.txt` i klikni **Run Pip Install**.
4. Restart. Prvi nalog koji se registruje je admin, zato ga odmah napravi.
5. `dd.db` i `secret.key` nastaju u app root-u pri prvom pokretanju. Ne brišu se pri deploy-u, a ne ulaze u git.

Opciono env promenljive: `DD_SECRET`, `DD_DB` (putanja do baze).

## Provera

```bash
pip install -r requirements.txt
python test_app.py
```
