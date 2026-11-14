# DD

Interna aplikacija za subotnju hranu. Flask + SQLite, bez frontend biblioteka.

## Uloge

| Uloga | Šta radi |
|---|---|
| **Gost** | Poručuje (subota 17:00–22:30), prati status porudžbine uživo, prijavljuje dolazak, glasa u anketama, ocenjuje hranu i uslugu |
| **DDejac** | Red (Plaćeno → Servirano), meni (jelo + prilozi), ankete, otprema račune posle smene, TV ekran |
| **Super admin** | Odobrava naloge (display name + uloga), menja uloge i lozinke, odblokira, vidi sve račune; može sve što i DDejac |

Prvi registrovani nalog je super admin. Postojeći admin iz prve verzije automatski postaje super admin.

**Red:** nikad nije jeo → jeo jednom → dodatne porudžbine. Ko je jeo 2 puta, blokiran je dok ga super admin ne odblokira.

**Obaveštenja:** stranice se same osvežavaju (polling na 4–8 s). Dodirni zvonce u zaglavlju da uključiš sistemska obaveštenja.

**TV:** tab Red → „Prikaži na TV-u“ daje link bez prijave.

## cPanel

1. **Setup Python App**: Python 3.9+, app root van javnog foldera (npr. `dd_app`), startup file `passenger_wsgi.py`, entry point `application`.
2. Otpremi fajlove u app root.
3. **Run Pip Install** za `requirements.txt`, pa **Restart**.
4. `dd.db`, `secret.key` i `uploads/` nastaju u app root-u. Ne briši ih pri novom deploy-u.

Opciono env: `DD_SECRET`, `DD_DB`, `DD_UPLOADS`.

## Provera

```bash
pip install -r requirements.txt
python test_app.py
```
