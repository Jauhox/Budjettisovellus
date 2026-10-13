import hashlib
import shutil
import os
from datetime import datetime, date
import sqlite3
import pandas as pd
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import calendar
import sqlalchemy

# ------------------------------------------------------------------------------
# TIETOKANNAN VALINTA & YLEISET MUUTTUJAT
# ------------------------------------------------------------------------------
DB_URL = st.secrets.get("DATABASE_URL") or os.getenv("DATABASE_URL")

DB_FILE = "budjetti.db"
BACKUP_FILE = "budjetti_varmuuskopio.db"
UNDO_FILE = "budjetti_undo.db"

if DB_URL:
    # Pakotetaan psycopg2-ajuriosaadi osoitteeseen, jos se puuttuu
    if "postgresql://" in DB_URL and "+psycopg2" not in DB_URL:
        DB_URL = DB_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    elif DB_URL.startswith("postgres://"):
        DB_URL = DB_URL.replace("postgres://", "postgresql+psycopg2://", 1)
        
    engine = sqlalchemy.create_engine(DB_URL)
    KAYTA_POSTGRES = True
else:
    KAYTA_POSTGRES = False

def saa_yhteys():
    if KAYTA_POSTGRES:
        return engine.connect()
    else:
        return sqlite3.connect(DB_FILE)

# ------------------------------------------------------------------------------
# 1. TIETOKANTAFUNKTIOT & VARMUUSKOPIOINTI / UNDO
# ------------------------------------------------------------------------------
def init_db():
    if KAYTA_POSTGRES:
        try:
            with engine.begin() as conn:
                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS tapahtumat (
                        id SERIAL PRIMARY KEY,
                        Päivämäärä TEXT,
                        Selite TEXT,
                        Summa REAL,
                        Kategoria TEXT,
                        Omistaja TEXT,
                        tiliote_id INTEGER,
                        kommentti TEXT
                    )
                """))
                try:
                    conn.execute(sqlalchemy.text("ALTER TABLE tapahtumat ADD COLUMN kommentti TEXT"))
                except Exception:
                    pass

                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS tiliotteet (
                        id SERIAL PRIMARY KEY,
                        tiedoston_nimi TEXT,
                        omistaja TEXT,
                        latausaika TEXT,
                        kohde_kk TEXT,
                        file_hash TEXT UNIQUE
                    )
                """))

                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS muistisaannot (
                        selite_avain TEXT PRIMARY KEY,
                        kategoria TEXT
                    )
                """))

                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS budjetit (
                        omistaja TEXT,
                        kategoria TEXT,
                        raja REAL,
                        PRIMARY KEY (omistaja, kategoria)
                    )
                """))

                conn.execute(sqlalchemy.text("""
                    CREATE TABLE IF NOT EXISTS kategoriat (
                        nimi TEXT PRIMARY KEY,
                        isanta_kategoria TEXT
                    )
                """))
                try:
                    conn.execute(sqlalchemy.text("ALTER TABLE kategoriat ADD COLUMN isanta_kategoria TEXT"))
                except Exception:
                    pass

                res = conn.execute(sqlalchemy.text("SELECT COUNT(*) FROM kategoriat")).fetchone()
                if res[0] == 0:
                    oletus_kategoriat = [
                        ("Tulo", None),
                        ("Sijoitus", None),
                        ("Ruoka", None), ("Ruokakauppa", "Ruoka"), ("Ravintolat & Wolt", "Ruoka"),
                        ("Auto", None), ("Polttoaine", "Auto"), ("Autohuolto & Katsastus", "Auto"),
                        ("Asuminen", None), ("Vuokra / Vastike", "Asuminen"), ("Sähkö & Vesi", "Asuminen"),
                        ("Lainan lyhennys", None),
                        ("Vapaa-aika", None), ("Terveys", None), ("Tavarat", None),
                        ("Matkustus", None), ("Satunnainen", None)
                    ]
                    for kat, isanta in oletus_kategoriat:
                        conn.execute(
                            sqlalchemy.text("INSERT INTO kategoriat (nimi, isanta_kategoria) VALUES (:nimi, :isanta) ON CONFLICT (nimi) DO NOTHING"),
                            {"nimi": kat, "isanta": isanta}
                        )
            alusta_oletus_budjetit()
        except Exception as e:
            st.error(f"Tietokannan alustusvirhe: {e}")
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()

        c.execute("""
            CREATE TABLE IF NOT EXISTS tapahtumat (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                Päivämäärä TEXT,
                Selite TEXT,
                Summa REAL,
                Kategoria TEXT,
                Omistaja TEXT,
                tiliote_id INTEGER,
                kommentti TEXT
            )
        """)

        try:
            c.execute("ALTER TABLE tapahtumat ADD COLUMN kommentti TEXT")
        except sqlite3.OperationalError:
            pass

        c.execute("""
            CREATE TABLE IF NOT EXISTS tiliotteet (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tiedoston_nimi TEXT,
                omistaja TEXT,
                latausaika TEXT,
                kohde_kk TEXT,
                file_hash TEXT UNIQUE
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS muistisaannot (
                selite_avain TEXT PRIMARY KEY,
                kategoria TEXT
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS budjetit (
                omistaja TEXT,
                kategoria TEXT,
                raja REAL,
                PRIMARY KEY (omistaja, kategoria)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS kategoriat (
                nimi TEXT PRIMARY KEY,
                isanta_kategoria TEXT
            )
        """)

        try:
            c.execute("ALTER TABLE kategoriat ADD COLUMN isanta_kategoria TEXT")
        except sqlite3.OperationalError:
            pass

        c.execute("SELECT COUNT(*) FROM kategoriat")
        if c.fetchone()[0] == 0:
            oletus_kategoriat = [
                ("Tulo", None),
                ("Sijoitus", None),
                ("Ruoka", None), ("Ruokakauppa", "Ruoka"), ("Ravintolat & Wolt", "Ruoka"),
                ("Auto", None), ("Polttoaine", "Auto"), ("Autohuolto & Katsastus", "Auto"),
                ("Asuminen", None), ("Vuokra / Vastike", "Asuminen"), ("Sähkö & Vesi", "Asuminen"),
                ("Lainan lyhennys", None),
                ("Vapaa-aika", None), ("Terveys", None), ("Tavarat", None),
                ("Matkustus", None), ("Satunnainen", None)
            ]
            for kat, isanta in oletus_kategoriat:
                c.execute("INSERT OR IGNORE INTO kategoriat (nimi, isanta_kategoria) VALUES (?, ?)", (kat, isanta))

        conn.commit()
        conn.close()
        alusta_oletus_budjetit()


def hae_kategoriat_db():
    if KAYTA_POSTGRES:
        with engine.connect() as conn:
            res = conn.execute(sqlalchemy.text("SELECT nimi, isanta_kategoria FROM kategoriat")).fetchall()
            return list(res)
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT nimi, isanta_kategoria FROM kategoriat")
        rows = c.fetchall()
        conn.close()
        return rows


def lisaa_kategoria_db(uusi_kategoria, isanta_kategoria=None):
    uusi_kategoria = uusi_kategoria.strip()
    if not uusi_kategoria:
        return False, "Kategorian nimi ei voi olla tyhjä!"

    if isanta_kategoria == "Ei mitään (Pääkategoria)":
        isanta_kategoria = None

    tee_varmuuskopio(UNDO_FILE)
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            try:
                conn.execute(
                    sqlalchemy.text("INSERT INTO kategoriat (nimi, isanta_kategoria) VALUES (:nimi, :isanta)"),
                    {"nimi": uusi_kategoria, "isanta": isanta_kategoria}
                )
                if uusi_kategoria != "Tulo":
                    for om in ["Juho", "Veera"]:
                        conn.execute(
                            sqlalchemy.text("INSERT INTO budjetit (omistaja, kategoria, raja) VALUES (:om, :kat, 0.0) ON CONFLICT (omistaja, kategoria) DO NOTHING"),
                            {"om": om, "kat": uusi_kategoria}
                        )
                return True, f"Kategoria '{uusi_kategoria}' lisätty!"
            except Exception as e:
                return False, f"Kategoria '{uusi_kategoria}' on jo olemassa!"
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO kategoriat (nimi, isanta_kategoria) VALUES (?, ?)", (uusi_kategoria, isanta_kategoria))
            if uusi_kategoria != "Tulo":
                c.execute("INSERT OR IGNORE INTO budjetit (omistaja, kategoria, raja) VALUES (?, ?, ?)", ("Juho", uusi_kategoria, 0.0))
                c.execute("INSERT OR IGNORE INTO budjetit (omistaja, kategoria, raja) VALUES (?, ?, ?)", ("Veera", uusi_kategoria, 0.0))
            conn.commit()
            conn.close()
            return True, f"Kategoria '{uusi_kategoria}' lisätty!"
        except sqlite3.IntegrityError:
            conn.close()
            return False, f"Kategoria '{uusi_kategoria}' on jo olemassa!"


def poista_kategoria_db(poistettava_kategoria):
    if not poistettava_kategoria or poistettava_kategoria == "Satunnainen":
        return False, "Kategoriaa 'Satunnainen' ei voi poistaa."

    tee_varmuuskopio(UNDO_FILE)
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text("DELETE FROM kategoriat WHERE nimi = :kat"), {"kat": poistettava_kategoria})
            conn.execute(sqlalchemy.text("UPDATE tapahtumat SET Kategoria = 'Satunnainen' WHERE Kategoria = :kat"), {"kat": poistettava_kategoria})
            conn.execute(sqlalchemy.text("DELETE FROM budjetit WHERE kategoria = :kat"), {"kat": poistettava_kategoria})
            conn.execute(sqlalchemy.text("UPDATE muistisaannot SET kategoria = 'Satunnainen' WHERE kategoria = :kat"), {"kat": poistettava_kategoria})
            conn.execute(sqlalchemy.text("UPDATE kategoriat SET isanta_kategoria = NULL WHERE isanta_kategoria = :kat"), {"kat": poistettava_kategoria})
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM kategoriat WHERE nimi = ?", (poistettava_kategoria,))
        c.execute("UPDATE tapahtumat SET Kategoria = 'Satunnainen' WHERE Kategoria = ?", (poistettava_kategoria,))
        c.execute("DELETE FROM budjetit WHERE kategoria = ?", (poistettava_kategoria,))
        c.execute("UPDATE muistisaannot SET kategoria = 'Satunnainen' WHERE kategoria = ?", (poistettava_kategoria,))
        c.execute("UPDATE kategoriat SET isanta_kategoria = NULL WHERE isanta_kategoria = ?", (poistettava_kategoria,))
        conn.commit()
        conn.close()
    return True, f"Kategoria '{poistettava_kategoria}' poistettu onnistuneesti!"


def alusta_oletus_budjetit():
    oletus_budjetit = {
        "Juho": {
            "Sijoitus": 200.0,
            "Ruokakauppa": 200.0, "Ravintolat & Wolt": 100.0,
            "Polttoaine": 150.0, "Autohuolto & Katsastus": 100.0,
            "Vapaa-aika": 150.0, "Tavarat": 100.0,
            "Sähkö & Vesi": 50.0, "Vuokra / Vastike": 150.0,
            "Lainan lyhennys": 500.0,
            "Terveys": 50.0, "Matkustus": 100.0, "Satunnainen": 100.0
        },
        "Veera": {
            "Sijoitus": 100.0,
            "Ruokakauppa": 200.0, "Ravintolat & Wolt": 100.0,
            "Polttoaine": 50.0,
            "Vapaa-aika": 150.0, "Tavarat": 100.0,
            "Sähkö & Vesi": 50.0, "Vuokra / Vastike": 150.0,
            "Lainan lyhennys": 500.0,
            "Terveys": 50.0, "Matkustus": 100.0, "Satunnainen": 100.0
        }
    }
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            for omistaja, kat_dict in oletus_budjetit.items():
                for kategoria, raja in kat_dict.items():
                    conn.execute(
                        sqlalchemy.text("""
                            INSERT INTO budjetit (omistaja, kategoria, raja) 
                            VALUES (:om, :kat, :raja) 
                            ON CONFLICT (omistaja, kategoria) DO NOTHING
                        """),
                        {"om": omistaja, "kat": kategoria, "raja": raja}
                    )
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        for omistaja, kat_dict in oletus_budjetit.items():
            for kategoria, raja in kat_dict.items():
                c.execute(
                    "INSERT OR IGNORE INTO budjetit (omistaja, kategoria, raja) VALUES (?, ?, ?)",
                    (omistaja, kategoria, raja)
                )
        conn.commit()
        conn.close()


def hae_budjetit_db():
    if KAYTA_POSTGRES:
        with engine.connect() as conn:
            rows = conn.execute(sqlalchemy.text("SELECT omistaja, kategoria, raja FROM budjetit")).fetchall()
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT omistaja, kategoria, raja FROM budjetit")
        rows = c.fetchall()
        conn.close()

    budjetit = {"Juho": {}, "Veera": {}}
    for omistaja, kategoria, raja in rows:
        if omistaja in budjetit:
            budjetit[omistaja][kategoria] = raja
    return budjetit


def paivita_budjetit_db(uudet_budjetit):
    tee_varmuuskopio(UNDO_FILE)
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            for omistaja, kat_dict in uudet_budjetit.items():
                for kategoria, raja in kat_dict.items():
                    conn.execute(
                        sqlalchemy.text("""
                            INSERT INTO budjetit (omistaja, kategoria, raja)
                            VALUES (:om, :kat, :raja)
                            ON CONFLICT(omistaja, kategoria) DO UPDATE SET raja = EXCLUDED.raja
                        """),
                        {"om": omistaja, "kat": kategoria, "raja": raja}
                    )
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        for omistaja, kat_dict in uudet_budjetit.items():
            for kategoria, raja in kat_dict.items():
                c.execute(
                    """
                    INSERT INTO budjetit (omistaja, kategoria, raja)
                    VALUES (?, ?, ?)
                    ON CONFLICT(omistaja, kategoria) DO UPDATE SET raja=excluded.raja
                """,
                    (omistaja, kategoria, raja),
                )
        conn.commit()
        conn.close()


def tee_varmuuskopio(target_path=UNDO_FILE):
    if not KAYTA_POSTGRES and os.path.exists(DB_FILE):
        shutil.copyfile(DB_FILE, target_path)


def palauta_varmuuskopio(source_path=UNDO_FILE):
    if not KAYTA_POSTGRES and os.path.exists(source_path):
        shutil.copyfile(source_path, DB_FILE)
        return True
    return False


def laske_tiedosto_hash(file_bytes):
    return hashlib.md5(file_bytes).hexdigest()


def hae_muistisaannot():
    if KAYTA_POSTGRES:
        with engine.connect() as conn:
            rows = conn.execute(sqlalchemy.text("SELECT selite_avain, kategoria FROM muistisaannot")).fetchall()
            return {row[0]: row[1] for row in rows}
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT selite_avain, kategoria FROM muistisaannot")
        rows = c.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}


def tallenna_muistisaanto(selite, kategoria, conn_obj=None):
    avain = str(selite).strip().lower()
    if not avain or kategoria == "Satunnainen":
        return

    if KAYTA_POSTGRES:
        if conn_obj is None:
            with engine.begin() as c:
                c.execute(
                    sqlalchemy.text("""
                        INSERT INTO muistisaannot (selite_avain, kategoria)
                        VALUES (:avain, :kat)
                        ON CONFLICT(selite_avain) DO UPDATE SET kategoria = EXCLUDED.kategoria
                    """),
                    {"avain": avain, "kat": kategoria}
                )
        else:
            conn_obj.execute(
                sqlalchemy.text("""
                    INSERT INTO muistisaannot (selite_avain, kategoria)
                    VALUES (:avain, :kat)
                    ON CONFLICT(selite_avain) DO UPDATE SET kategoria = EXCLUDED.kategoria
                """),
                {"avain": avain, "kat": kategoria}
            )
    else:
        sulje_yhteys = False
        if conn_obj is None:
            conn_obj = sqlite3.connect(DB_FILE)
            sulje_yhteys = True

        c = conn_obj.cursor()
        c.execute(
            """
            INSERT INTO muistisaannot (selite_avain, kategoria)
            VALUES (?, ?)
            ON CONFLICT(selite_avain) DO UPDATE SET kategoria=excluded.kategoria
        """,
            (avain, kategoria),
        )

        if sulje_yhteys:
            conn_obj.commit()
            conn_obj.close()


def lataa_tapahtumat_db():
    if KAYTA_POSTGRES:
        with engine.connect() as conn:
            df = pd.read_sql_query(
                sqlalchemy.text("SELECT id, Päivämäärä, Selite, Summa, Kategoria, Omistaja, tiliote_id, kommentti FROM tapahtumat"),
                conn
            )
    else:
        conn = sqlite3.connect(DB_FILE)
        df = pd.read_sql_query(
            "SELECT id, Päivämäärä, Selite, Summa, Kategoria, Omistaja, tiliote_id, kommentti FROM tapahtumat",
            conn,
        )
        conn.close()

    if not df.empty:
        df["Pvm_dt"] = pd.to_datetime(df["Päivämäärä"], dayfirst=True, errors="coerce")
        df["Päivämäärä"] = df["Pvm_dt"].dt.strftime("%d-%m-%Y").fillna(df["Päivämäärä"])
        df = df.sort_values(by="Pvm_dt", ascending=False).reset_index(drop=True)
    return df


def hae_ladatut_tiliotteet():
    if KAYTA_POSTGRES:
        with engine.connect() as conn:
            df = pd.read_sql_query(
                sqlalchemy.text("SELECT id, tiedoston_nimi, omistaja, latausaika, kohde_kk, file_hash FROM tiliotteet ORDER BY kohde_kk DESC, latausaika DESC"),
                conn
            )
    else:
        conn = sqlite3.connect(DB_FILE)
        df = pd.read_sql_query(
            "SELECT id, tiedoston_nimi, omistaja, latausaika, kohde_kk, file_hash FROM tiliotteet ORDER BY kohde_kk DESC, latausaika DESC",
            conn,
        )
        conn.close()
    return df


def paattele_tiliotteen_kuukausi(df_uusi):
    if "Päivämäärä" not in df_uusi.columns:
        return datetime.now().strftime("%Y-%m")
    
    pvmt = pd.to_datetime(df_uusi["Päivämäärä"], dayfirst=True, errors="coerce").dropna()
    if pvmt.empty:
        return datetime.now().strftime("%Y-%m")
    
    kk_sarja = pvmt.dt.strftime("%Y-%m")
    yleisimmat = kk_sarja.mode()
    
    if not yleisimmat.empty:
        return yleisimmat.iloc[0]
    return pvmt.iloc[0].strftime("%Y-%m")


def tallenna_tiliote_ja_tapahtumat(df_uusi, tiedoston_nimi, omistaja, file_bytes):
    file_hash = laske_tiedosto_hash(file_bytes)
    tee_varmuuskopio(UNDO_FILE)
    kohde_kk = paattele_tiliotteen_kuukausi(df_uusi)

    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            try:
                res = conn.execute(
                    sqlalchemy.text("""
                        INSERT INTO tiliotteet (tiedoston_nimi, omistaja, latausaika, kohde_kk, file_hash)
                        VALUES (:tnimi, :om, :lataus, :kk, :fhash)
                        RETURNING id
                    """),
                    {
                        "tnimi": tiedoston_nimi,
                        "om": omistaja,
                        "lataus": datetime.now().strftime("%Y-%m-%d %H:%M"),
                        "kk": kohde_kk,
                        "fhash": file_hash
                    }
                ).fetchone()
                tiliote_id = res[0]
            except Exception:
                return False, "Tämä tiliote on jo ladattu sovellukseen (tuplalataus estetty)!"

            df_tallenna = df_uusi.copy()
            df_tallenna["tiliote_id"] = tiliote_id
            df_tallenna["kommentti"] = ""
            df_tallenna = df_tallenna.drop(columns=["id", "Pvm_dt"], errors="ignore")
            df_tallenna.to_sql("tapahtumat", con=conn, if_exists="append", index=False)
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        try:
            c.execute(
                """
                INSERT INTO tiliotteet (tiedoston_nimi, omistaja, latausaika, kohde_kk, file_hash)
                VALUES (?, ?, ?, ?, ?)
            """,
                (tiedoston_nimi, omistaja, datetime.now().strftime("%Y-%m-%d %H:%M"), kohde_kk, file_hash),
            )
            tiliote_id = c.lastrowid
        except sqlite3.IntegrityError:
            conn.close()
            return False, "Tämä tiliote on jo ladattu sovellukseen (tuplalataus estetty)!"

        df_tallenna = df_uusi.copy()
        df_tallenna["tiliote_id"] = tiliote_id
        df_tallenna["kommentti"] = ""
        df_tallenna = df_tallenna.drop(columns=["id", "Pvm_dt"], errors="ignore")
        df_tallenna.to_sql("tapahtumat", conn, if_exists="append", index=False)
        conn.commit()
        conn.close()

    return True, f"Tiliote '{tiedoston_nimi}' sijoitettiin automaattisesti kuukauden {kohde_kk} slottiin!"


def poista_tiliote(tiliote_id):
    tee_varmuuskopio(UNDO_FILE)
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text("DELETE FROM tapahtumat WHERE tiliote_id = :tid"), {"tid": tiliote_id})
            conn.execute(sqlalchemy.text("DELETE FROM tiliotteet WHERE id = :tid"), {"tid": tiliote_id})
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM tapahtumat WHERE tiliote_id = ?", (tiliote_id,))
        c.execute("DELETE FROM tiliotteet WHERE id = ?", (tiliote_id,))
        conn.commit()
        conn.close()


def poista_yksittaiset_tapahtumat(tapahtuma_id_list):
    if not tapahtuma_id_list:
        return
    tee_varmuuskopio(UNDO_FILE)
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            for tid in tapahtuma_id_list:
                conn.execute(sqlalchemy.text("DELETE FROM tapahtumat WHERE id = :tid"), {"tid": tid})
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.executemany("DELETE FROM tapahtumat WHERE id = ?", [(tid,) for tid in tapahtuma_id_list])
        conn.commit()
        conn.close()


def lisaa_yksittainen_tapahtuma_db(pvm_str, omistaja, selite, summa, kategoria, kommentti):
    tee_varmuuskopio(UNDO_FILE)
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            conn.execute(
                sqlalchemy.text("""
                    INSERT INTO tapahtumat (Päivämäärä, Selite, Summa, Kategoria, Omistaja, tiliote_id, kommentti)
                    VALUES (:pvm, :sel, :sum, :kat, :om, NULL, :kom)
                """),
                {"pvm": pvm_str, "sel": selite, "sum": summa, "kat": kategoria, "om": omistaja, "kom": kommentti}
            )
            tallenna_muistisaanto(selite, kategoria, conn_obj=conn)
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO tapahtumat (Päivämäärä, Selite, Summa, Kategoria, Omistaja, tiliote_id, kommentti)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
        """,
            (pvm_str, selite, summa, kategoria, omistaja, kommentti),
        )
        tallenna_muistisaanto(selite, kategoria, conn_obj=conn)
        conn.commit()
        conn.close()


def muotoile_pvm(pvm_arvo):
    if pd.isna(pvm_arvo) or str(pvm_arvo).strip() == "":
        return datetime.now().strftime("%d-%m-%Y")
    
    if isinstance(pvm_arvo, (datetime, pd.Timestamp, date)):
        return pvm_arvo.strftime("%d-%m-%Y")
    
    parsed = pd.to_datetime(str(pvm_arvo), dayfirst=True, errors="coerce")
    if pd.notna(parsed):
        return parsed.strftime("%d-%m-%Y")
    
    return str(pvm_arvo)


def paivita_tapahtumat_db(df_paivitetty):
    tee_varmuuskopio(UNDO_FILE)
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            for _, row in df_paivitetty.dropna(subset=["id"]).iterrows():
                pvm_korjattu = muotoile_pvm(row["Päivämäärä"])
                kommentti_arvo = str(row.get("kommentti", "")) if pd.notna(row.get("kommentti", "")) else ""
                conn.execute(
                    sqlalchemy.text("""
                        UPDATE tapahtumat 
                        SET Päivämäärä = :pvm, Selite = :sel, Summa = :sum, Kategoria = :kat, Omistaja = :om, kommentti = :kom
                        WHERE id = :id
                    """),
                    {
                        "pvm": pvm_korjattu,
                        "sel": row["Selite"],
                        "sum": row["Summa"],
                        "kat": row["Kategoria"],
                        "om": row["Omistaja"],
                        "kom": kommentti_arvo,
                        "id": int(row["id"])
                    }
                )
                tallenna_muistisaanto(row["Selite"], row["Kategoria"], conn_obj=conn)

            uudet_rivit = df_paivitetty[df_paivitetty["id"].isna()]
            for _, row in uudet_rivit.iterrows():
                pvm_korjattu = muotoile_pvm(row["Päivämäärä"])
                kommentti_arvo = str(row.get("kommentti", "")) if pd.notna(row.get("kommentti", "")) else ""
                if str(row["Selite"]).strip() != "" and pd.notna(row["Summa"]):
                    conn.execute(
                        sqlalchemy.text("""
                            INSERT INTO tapahtumat (Päivämäärä, Selite, Summa, Kategoria, Omistaja, tiliote_id, kommentti)
                            VALUES (:pvm, :sel, :sum, :kat, :om, NULL, :kom)
                        """),
                        {
                            "pvm": pvm_korjattu,
                            "sel": row["Selite"],
                            "sum": row["Summa"],
                            "kat": row["Kategoria"],
                            "om": row["Omistaja"],
                            "kom": kommentti_arvo
                        }
                    )
                    tallenna_muistisaanto(row["Selite"], row["Kategoria"], conn_obj=conn)
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        for _, row in df_paivitetty.dropna(subset=["id"]).iterrows():
            pvm_korjattu = muotoile_pvm(row["Päivämäärä"])
            kommentti_arvo = str(row.get("kommentti", "")) if pd.notna(row.get("kommentti", "")) else ""
            c.execute(
                """
                UPDATE tapahtumat 
                SET Päivämäärä = ?, Selite = ?, Summa = ?, Kategoria = ?, Omistaja = ?, kommentti = ?
                WHERE id = ?
            """,
                (
                    pvm_korjattu,
                    row["Selite"],
                    row["Summa"],
                    row["Kategoria"],
                    row["Omistaja"],
                    kommentti_arvo,
                    int(row["id"]),
                ),
            )
            tallenna_muistisaanto(row["Selite"], row["Kategoria"], conn_obj=conn)

        uudet_rivit = df_paivitetty[df_paivitetty["id"].isna()]
        for _, row in uudet_rivit.iterrows():
            pvm_korjattu = muotoile_pvm(row["Päivämäärä"])
            kommentti_arvo = str(row.get("kommentti", "")) if pd.notna(row.get("kommentti", "")) else ""
            if str(row["Selite"]).strip() != "" and pd.notna(row["Summa"]):
                c.execute(
                    """
                    INSERT INTO tapahtumat (Päivämäärä, Selite, Summa, Kategoria, Omistaja, tiliote_id, kommentti)
                    VALUES (?, ?, ?, ?, ?, NULL, ?)
                """,
                    (
                        pvm_korjattu,
                        row["Selite"],
                        row["Summa"],
                        row["Kategoria"],
                        row["Omistaja"],
                        kommentti_arvo,
                    ),
                )
                tallenna_muistisaanto(row["Selite"], row["Kategoria"], conn_obj=conn)

        conn.commit()
        conn.close()


def nollaa_tietokanta():
    tee_varmuuskopio(BACKUP_FILE)
    tee_varmuuskopio(UNDO_FILE)
    if KAYTA_POSTGRES:
        with engine.begin() as conn:
            conn.execute(sqlalchemy.text("DELETE FROM tapahtumat"))
            conn.execute(sqlalchemy.text("DELETE FROM tiliotteet"))
            conn.execute(sqlalchemy.text("DELETE FROM muistisaannot"))
            conn.execute(sqlalchemy.text("DELETE FROM budjetit"))
            conn.execute(sqlalchemy.text("DELETE FROM kategoriat"))
    else:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("DELETE FROM tapahtumat")
        c.execute("DELETE FROM tiliotteet")
        c.execute("DELETE FROM muistisaannot")
        c.execute("DELETE FROM budjetit")
        c.execute("DELETE FROM kategoriat")
        conn.commit()
        conn.close()
    init_db()


init_db()

# ------------------------------------------------------------------------------
# 2. APUFUNKTIO OMISTAJASUODATUKSEEN (JAKAA "Molemmat" PUOLIKSI)
# ------------------------------------------------------------------------------
def suodata_tapahtumat_henkilolle(df, nakyma_valinta):
    if df.empty:
        return df
    
    if nakyma_valinta == "Yhdistetty (Juho + Veera)":
        return df.copy()
    
    df_Oma = df[df["Omistaja"] == nakyma_valinta].copy()
    df_Molemmat = df[df["Omistaja"] == "Molemmat (Juho & Veera)"].copy()
    
    if not df_Molemmat.empty:
        df_Molemmat["Summa"] = df_Molemmat["Summa"] / 2.0
        df_Molemmat["Omistaja"] = nakyma_valinta
        
    df_yhdistetty = pd.concat([df_Oma, df_Molemmat], ignore_index=True)
    if not df_yhdistetty.empty and "Pvm_dt" in df_yhdistetty.columns:
        df_yhdistetty = df_yhdistetty.sort_values(by="Pvm_dt", ascending=False).reset_index(drop=True)
    return df_yhdistetty


# ------------------------------------------------------------------------------
# 3. AUTOMAATTINEN KATEGORISOINTI & SIVUPALKKI
# ------------------------------------------------------------------------------
KATEGORIA_SAANNOT = {
    "Tulo": ["palkka", "palkat", "kela", "veronpalautus", "osinko", "korvaus", "stipendi"],
    "Lainan lyhennys": ["lainan lyhennys", "lyhennys", "asuntolaina", "korko ja kulut", "laina"],
    "Sijoitus": ["nordnet", "nordea investor", "rahasto", "osake", "säästö", "seligson", "coinbase"],
    "Ruokakauppa": ["citymarket", "k-market", "supermarket", "s-market", "prisma", "alepa", "lidl"],
    "Ravintolat & Wolt": ["ravintola", "hesburger", "mcdonalds", "wolt", "foodora", "alko"],
    "Polttoaine": ["neste", "abc", "st1", "teboil", "seot", "bensa", "polttoaine"],
    "Autohuolto & Katsastus": ["katsastus", "car washes", "rengas"],
    "Vuokra / Vastike": ["vuokra", "hoitovastike", "rahoitusvastike", "yhtiövastike", "vastike"],
    "Sähkö & Vesi": ["vesimaksu", "sähkö", "helen", "caruna", "fortum"],
    "Vapaa-aika": ["spotify", "netflix", "hbo", "disney", "google play", "finnkino", "lippu", "kuntosali"],
    "Terveys": ["apteekki", "drug stores", "pharmacies", "yths", "terveystalo", "mehiläinen", "hammas"],
    "Tavarat": ["ikea", "jysk", "gigantti", "power", "clas ohlson", "tokmanni"],
    "Matkustus": ["hsl", "vr", "finnair", "norwegian", "booking", "airbnb", "hotelli"],
}


def tunnista_kategoria(selite, summa=0.0, merchant_category=""):
    selite_avain = str(selite).strip().lower()
    muistisaannot = hae_muistisaannot()

    if summa > 0 and selite_avain not in muistisaannot:
        return "Tulo"

    if selite_avain in muistisaannot:
        return muistisaannot[selite_avain]

    for opittu_selite, opittu_kat in muistisaannot.items():
        if opittu_selite in selite_avain:
            return opittu_kat

    tarkistettava_teksti = f"{selite_avain} {str(merchant_category).lower()}".strip()
    for kategoria, avainsanat in KATEGORIA_SAANNOT.items():
        if any(kw in tarkistettava_teksti for kw in avainsanat):
            return kategoria

    return "Satunnainen"


def siivoa_tiliote(df, omistaja):
    pvm_hakusanat = ["transactiondate", "pvm", "päivä", "päivämäärä", "kirjauspäivä", "arvopäivä", "date"]
    selite_hakusanat = ["text", "selite", "teksti", "saaja/maksaja", "saaja", "maksaja", "nimi", "payee"]
    summa_hakusanat = ["amount", "summa", "määrä", "euroa", "eur"]
    merchant_col = [c for c in df.columns if "merchant category" in str(c).lower()]

    pvm_col = [c for c in df.columns if any(kw == str(c).lower().strip() for kw in pvm_hakusanat)] or [c for c in df.columns if any(kw in str(c).lower().strip() for kw in pvm_hakusanat)]
    selite_col = [c for c in df.columns if any(kw == str(c).lower().strip() for kw in selite_hakusanat)] or [c for c in df.columns if any(kw in str(c).lower().strip() for kw in selite_hakusanat)]
    summa_col = [c for c in df.columns if any(kw == str(c).lower().strip() for kw in summa_hakusanat)] or [c for c in df.columns if any(kw in str(c).lower().strip() for kw in summa_hakusanat)]

    if pvm_col and selite_col and summa_col:
        m_cat = df[merchant_col[0]] if merchant_col else [""] * len(df)
        df_clean = pd.DataFrame({
            "Päivämäärä": df[pvm_col[0]].astype(str),
            "Selite": df[selite_col[0]].astype(str),
            "Summa": df[summa_col[0]],
            "Omistaja": omistaja,
            "Merchant_Cat": m_cat
        })

        if not pd.api.types.is_numeric_dtype(df_clean["Summa"]):
            df_clean["Summa"] = (
                df_clean["Summa"]
                .astype(str)
                .str.replace("\xa0", "", regex=False)
                .str.replace(" ", "", regex=False)
                .str.replace(",", ".", regex=False)
            )
            df_clean["Summa"] = pd.to_numeric(df_clean["Summa"], errors="coerce")

        pvm_parsed = pd.to_datetime(df_clean["Päivämäärä"], dayfirst=True, errors="coerce")
        df_clean["Päivämäärä"] = pvm_parsed.dt.strftime("%d-%m-%Y").fillna(df_clean["Päivämäärä"])

        df_clean["Kategoria"] = df_clean.apply(lambda r: tunnista_kategoria(r["Selite"], r["Summa"], r["Merchant_Cat"]), axis=1)
        df_clean["kommentti"] = ""
        df_clean = df_clean.drop(columns=["Merchant_Cat"])
        df_clean = df_clean.dropna(subset=["Summa"])
        return df_clean

    return pd.DataFrame()


st.set_page_config(page_title="Juhon & Veeran Budjettisovellus", layout="wide")
st.title("💵 Juhon & Veeran Budjettisovellus")

if "tapahtumat" not in st.session_state or st.session_state.get("paivita_db", False):
    st.session_state["tapahtumat"] = lataa_tapahtumat_db()
    st.session_state["tiliotteet"] = hae_ladatut_tiliotteet()
    st.session_state["budjetit"] = hae_budjetit_db()
    st.session_state["kategoriat_raw"] = hae_kategoriat_db()
    st.session_state["paivita_db"] = False

kat_raw = st.session_state.get("kategoriat_raw", hae_kategoriat_db())
KATEGORIAT_LISTA = [row[0] for row in kat_raw]
PAAKATEGORIAT = [row[0] for row in kat_raw if row[1] is None]
ISANTA_MAP = {row[0]: row[1] for row in kat_raw if row[1] is not None}
BUDJETIT = st.session_state.get("budjetit", hae_budjetit_db())

st.sidebar.header("⚙ Asetukset & Hallinta")

if not KAYTA_POSTGRES and os.path.exists(UNDO_FILE):
    if st.sidebar.button("↩️ Kumoa edellinen toiminto"):
        if palauta_varmuuskopio(UNDO_FILE):
            st.session_state["paivita_db"] = True
            st.sidebar.success("Edellinen toiminto kumottu!")
            st.rerun()

nakyma = st.sidebar.selectbox("Henkilönäkymä:", ["Yhdistetty (Juho + Veera)", "Juho", "Veera"])

st.sidebar.markdown("---")
with st.sidebar.expander("➕ Lisää yksittäinen tapahtuma", expanded=False):
    with st.form("lisaa_yksittainen_form", clear_on_submit=True):
        uusi_pvm = st.date_input("Päivämäärä:", value=date.today())
        uusi_omistaja = st.selectbox("Omistaja:", ["Juho", "Veera", "Molemmat (Juho & Veera)"])
        uusi_selite = st.text_input("Selite:")
        uusi_summa = st.number_input("Summa (€) (miinus = meno, plus = tulo):", value=0.0, step=1.0)
        uusi_kat = st.selectbox("Kategoria:", KATEGORIAT_LISTA)
        uusi_kommentti = st.text_input("Kommentti (valinnainen):")
        
        lisaa_tapahtuma_sub = st.form_submit_button("💾 Tallenna tapahtuma")
        if lisaa_tapahtuma_sub:
            if not uusi_selite.strip():
                st.error("Selite ei voi olla tyhjä!")
            else:
                pvm_formatted = uusi_pvm.strftime("%d-%m-%Y")
                lisaa_yksittainen_tapahtuma_db(pvm_formatted, uusi_omistaja, uusi_selite.strip(), uusi_summa, uusi_kat, uusi_kommentti.strip())
                st.session_state["paivita_db"] = True
                st.success("Tapahtuma lisätty onnistuneesti!")
                st.rerun()

st.sidebar.markdown("---")
with st.sidebar.expander("🏷️ Hallitse kategorioita", expanded=False):
    st.subheader("Lisää uusi kategoria")
    with st.form("lisaa_kat_form", clear_on_submit=True):
        uusi_kat_nimi = st.text_input("Kategorian nimi:")
        valittu_isanta = st.selectbox(
            "Valitse yläkategoria (tai tee pääkategoria):",
            ["Ei mitään (Pääkategoria)"] + PAAKATEGORIAT
        )
        lisaa_sub = st.form_submit_button("➕ Lisää kategoria")
        if lisaa_sub:
            ok, msg = lisaa_kategoria_db(uusi_kat_nimi, valittu_isanta)
            if ok:
                st.session_state["paivita_db"] = True
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    st.markdown("---")
    st.subheader("Poista kategoria")
    poistettavat_optiot = [k for k in KATEGORIAT_LISTA if k != "Satunnainen"]
    if poistettavat_optiot:
        with st.form("poista_kat_form"):
            poistettava_kat = st.selectbox("Valitse poistettava kategoria:", poistettavat_optiot)
            poista_sub = st.form_submit_button("🗑️ Poista kategoria")
            if poista_sub:
                ok, msg = poista_kategoria_db(poistettava_kat)
                if ok:
                    st.session_state["paivita_db"] = True
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(msg)

st.sidebar.markdown("---")
with st.sidebar.expander("✏️ Muokkaa kuukausibudjetteja", expanded=False):
    muokattava_henkilo = st.radio("Valitse muokattava henkilö:", ["Juho", "Veera"], key="b_henkilo")
    uudet_rajat = {}
    
    muokattavat_kategoriat = ["Sijoitus"] + [k for k in KATEGORIAT_LISTA if k != "Tulo" and k not in PAAKATEGORIAT]
    
    st.caption("💡 Anna budjetit **kuukausitasolla (€/kk)**.")
    for kat in muokattavat_kategoriat:
        nykyinen_raja = BUDJETIT.get(muokattava_henkilo, {}).get(kat, 0.0)
        uudet_rajat[kat] = st.number_input(f"{kat} (€/kk)", min_value=0.0, value=float(nykyinen_raja), step=10.0, key=f"input_b_{muokattava_henkilo}_{kat}")
    
    if st.button(f"💾 Tallenna henkilön {muokattava_henkilo} kuukausibudjetit"):
        paivitetyt_budjetit = BUDJETIT.copy()
        paivitetyt_budjetit[muokattava_henkilo] = uudet_rajat
        paivita_budjetit_db(paivitetyt_budjetit)
        st.session_state["paivita_db"] = True
        st.sidebar.success(f"Kuukausibudjetit tallennettu!")
        st.rerun()

st.sidebar.markdown("---")
st.sidebar.subheader("📥 Lataa uusi tiliote")
omistaja_valinta = st.sidebar.radio("Tiliotteen omistaja:", ["Juho", "Veera", "Molemmat (Juho & Veera)"])
uploaded_file = st.sidebar.file_uploader("Valitse CSV tai Excel -tiedosto", type=["xlsx", "csv"])

if uploaded_file is not None:
    file_bytes = uploaded_file.getvalue()
    try:
        if uploaded_file.name.lower().endswith(".csv"):
            df_uusi = pd.DataFrame()
            for encoding in ["utf-8", "iso-8859-1", "latin1"]:
                for sep in [";", ","]:
                    try:
                        uploaded_file.seek(0)
                        df_temp = pd.read_csv(uploaded_file, sep=sep, encoding=encoding)
                        if len(df_temp.columns) > 1:
                            df_uusi = df_temp
                            break
                    except Exception:
                        continue
                if not df_uusi.empty:
                    break
        else:
            df_uusi = pd.read_excel(uploaded_file)

        df_siivottu = siivoa_tiliote(df_uusi, omistaja_valinta)
        if not df_siivottu.empty:
            if st.sidebar.button(f"Tallenna tiliote omistajalle {omistaja_valinta}"):
                ok, viesti = tallenna_tiliote_ja_tapahtumat(df_siivottu, uploaded_file.name, omistaja_valinta, file_bytes)
                if ok:
                    st.session_state["paivita_db"] = True
                    st.sidebar.success(viesti)
                    st.rerun()
                else:
                    st.sidebar.warning(viesti)
        else:
            st.sidebar.error("Tiliotteesta ei löytynyt tarvittavia sarakkeita.")
    except Exception as e:
        st.sidebar.error(f"Virhe: {e}")

st.sidebar.markdown("---")
with st.sidebar.expander("📂 Ladatut tiliotteet", expanded=False):
    df_tiliotteet = st.session_state.get("tiliotteet", pd.DataFrame())
    if not df_tiliotteet.empty:
        for kk, group in df_tiliotteet.groupby("kohde_kk"):
            st.markdown(f"**🗓️ {kk}**")
            for _, r in group.iterrows():
                col_n, col_d = st.columns([4, 1])
                col_n.text(f"• {r['tiedoston_nimi']} ({r['omistaja']})")
                if col_d.button("🗑️", key=f"del_{r['id']}", help="Poista tiliote"):
                    poista_tiliote(r["id"])
                    st.session_state["paivita_db"] = True
                    st.rerun()
            st.markdown("")
    else:
        st.caption("Ei ladattuja tiliotteita.")

st.header(f"📊 Näkymä: {nakyma}")
df_all = st.session_state.get("tapahtumat", pd.DataFrame())

df_person = suodata_tapahtumat_henkilolle(df_all, nakyma)

if nakyma == "Juho":
    aktiivinen_budjetti_kk = BUDJETIT.get("Juho", {})
elif nakyma == "Veera":
    aktiivinen_budjetti_kk = BUDJETIT.get("Veera", {})
else:
    juho_b = BUDJETIT.get("Juho", {})
    veera_b = BUDJETIT.get("Veera", {})
    aktiivinen_budjetti_kk = {k: juho_b.get(k, 0.0) + veera_b.get(k, 0.0) for k in set(juho_b) | set(veera_b)}

aktiivinen_budjetti_kk = {k: v for k, v in aktiivinen_budjetti_kk.items() if k in KATEGORIAT_LISTA}

tab1, tab2, tab3 = st.tabs(["📊 Budjetti & Tapahtumat", "🗓️ Vuosikatsaus & Vertailu", "📈 Kaaviot & Diagrammit"])

with tab1:
    st.subheader("🗓 Valitse tarkasteluajanjakso")
    col_aika1, col_aika2 = st.columns(2)
    with col_aika1:
        aikavali_tyyppi = st.selectbox("Suodata tapahtumia:", ["Kuukausittain", "Viikoittain", "Vuosittain", "Vuoden alusta (YTD)", "Kaikki tapahtumat"])

    df_filtr = df_person.copy()
    valittu_kk = None
    kuukausi_kerroin = 1.0

    if not df_person.empty and "Pvm_dt" in df_person.columns:
        df_valid = df_person.dropna(subset=["Pvm_dt"])
        if not df_valid.empty:
            nykyinen_vuosi = datetime.now().year
            with col_aika2:
                if aikavali_tyyppi == "Kuukausittain":
                    saatavat_kk = sorted(df_valid["Pvm_dt"].dt.strftime("%Y-%m").unique(), reverse=True)
                    valittu_kk = st.selectbox("Valitse kuukausi:", saatavat_kk)
                    df_filtr = df_person[df_person["Pvm_dt"].dt.strftime("%Y-%m") == valittu_kk]
                    kuukausi_kerroin = 1.0
                elif aikavali_tyyppi == "Viikoittain":
                    df_valid["Vuosi_Viikko"] = df_valid["Pvm_dt"].dt.strftime("%Y - Viikko %V")
                    saatavat_vk = sorted(df_valid["Vuosi_Viikko"].unique(), reverse=True)
                    valittu_vk = st.selectbox("Valitse viikko:", saatavat_vk)
                    df_filtr = df_valid[df_valid["Vuosi_Viikko"] == valittu_vk]
                    kuukausi_kerroin = 1.0 / 4.33
                elif aikavali_tyyppi == "Vuosittain":
                    saatavat_vuodet = sorted(df_valid["Pvm_dt"].dt.year.unique(), reverse=True)
                    valittu_vuosi = st.selectbox("Valitse vuosi:", saatavat_vuodet, key="aikavali_valittu_vuosi")
                    df_filtr = df_person[df_person["Pvm_dt"].dt.year == valittu_vuosi]
                    kuukausi_kerroin = 12.0
                elif aikavali_tyyppi == "Vuoden alusta (YTD)":
                    df_filtr = df_person[(df_person["Pvm_dt"].dt.year == nykyinen_vuosi) & (df_person["Pvm_dt"] <= datetime.now())]
                    kuluneet_kk = datetime.now().month
                    kuukausi_kerroin = float(kuluneet_kk)
                elif aikavali_tyyppi == "Kaikki tapahtumat":
                    kuukausi_kerroin = 1.0

    aktiivinen_budjetti = {k: v * kuukausi_kerroin for k, v in aktiivinen_budjetti_kk.items()}

    if aikavali_tyyppi == "Kuukausittain" and valittu_kk:
        st.markdown("---")
        st.subheader(f"📅 Kuukausikalenteri ({valittu_kk})")
        st.caption("💡 Klikkaa haluamaasi päivälaatikkoa nähdäksesi kyseisen päivän tapahtumat tarkemmin. Päivät, joilla on kommentti, on korostettu keltaisella taustalla ja 💬-merkillä.")
        
        vuosi_int, kk_int = map(int, valittu_kk.split("-"))
        kalenteri_matriisi = calendar.monthcalendar(vuosi_int, kk_int)
        
        kk_tapahtumat = df_person[df_person["Pvm_dt"].dt.strftime("%Y-%m") == valittu_kk].copy()
        
        paiva_summat = {}
        paiva_kommentit = {}
        if not kk_tapahtumat.empty:
            for day, group in kk_tapahtumat.groupby(kk_tapahtumat["Pvm_dt"].dt.day):
                netto = group["Summa"].sum()
                paiva_summat[day] = netto
                onko_kommenttia = any(group["kommentti"].notna() & (group["kommentti"].str.strip() != ""))
                paiva_kommentit[day] = onko_kommenttia

        viikonpaivat = ["Maanantai", "Tiistai", "Keskiviikko", "Torstai", "Perjantai", "Lauantai", "Sunnuntai"]
        cols = st.columns(7)
        for idx, pv_nimi in enumerate(viikonpaivat):
            cols[idx].markdown(f"<div style='text-align: center; font-weight: bold;'>{pv_nimi}</div>", unsafe_allow_html=True)

        if "valittu_kalenteri_pv" not in st.session_state:
            st.session_state["valittu_kalenteri_pv"] = None

        for viikko in kalenteri_matriisi:
            cols_v = st.columns(7)
            for i, pv in enumerate(viikko):
                with cols_v[i]:
                    if pv == 0:
                        st.markdown("<div style='text-align: center; color: #ccc; padding: 15px;'>-</div>", unsafe_allow_html=True)
                    else:
                        netto_pv = paiva_summat.get(pv, 0.0)
                        onko_kommenttia = paiva_kommentit.get(pv, False)
                        
                        kommentti_indikaattori = " 💬" if onko_kommenttia else ""
                        tausta_vari = "#fff9c4" if onko_kommenttia else "#f9f9f9"
                        reuna_vari = "#fbc02d" if onko_kommenttia else "#ddd"
                        
                        if netto_pv > 0:
                            summa_HTML = f"<div style='color: green; font-size: 0.85em; font-weight: bold;'>+{netto_pv:.1f} €</div>"
                        elif netto_pv < 0:
                            summa_HTML = f"<div style='color: red; font-size: 0.85em; font-weight: bold;'>{netto_pv:.1f} €</div>"
                        else:
                            summa_HTML = "<div style='color: #888; font-size: 0.85em;'>0.0 €</div>"
                        
                        card_html = f"""
                        <div style='border: 1px solid {reuna_vari}; border-radius: 5px; padding: 8px; text-align: center; background-color: {tausta_vari}; margin-bottom: 5px;'>
                            <div style='font-weight: bold; font-size: 1.1em;'>{pv}{kommentti_indikaattori}</div>
                            {summa_HTML}
                        </div>
                        """
                        st.markdown(card_html, unsafe_allow_html=True)
                        if st.button("Valitse", key=f"kal_pv_{vuosi_int}_{kk_int}_{pv}", use_container_width=True):
                            st.session_state["valittu_kalenteri_pv"] = f"{pv:02d}-{kk_int:02d}-{vuosi_int}"
                            st.rerun()

        valittu_pv_str = st.session_state.get("valittu_kalenteri_pv")
        if valittu_pv_str:
            pvm_tarkistus = datetime.strptime(valittu_pv_str, "%d-%m-%Y").strftime("%d-%m-%Y")
            tapahtumat_paivalle = kk_tapahtumat[kk_tapahtumat["Päivämäärä"] == pvm_tarkistus]
            
            st.markdown(f"**🔍 Päivän {valittu_pv_str} tapahtumat:**")
            if not tapahtumat_paivalle.empty:
                st.dataframe(tapahtumat_paivalle[["Päivämäärä", "Omistaja", "Selite", "Summa", "Kategoria", "kommentti"]], use_container_width=True, hide_index=True)
            else:
                st.info(f"Ei tapahtumia päivänä {valittu_pv_str}.")
            
            if st.button("Sulje päivän tarkastelu"):
                st.session_state["valittu_kalenteri_pv"] = None
                st.rerun()

    st.markdown("---")
    
    with st.expander("🔎 Hae tietyn maksun saajan / selitteen tapahtumat ja yhteissumma", expanded=True):
        col_h1, col_h2 = st.columns(2)
        with col_h1:
            kaikki_selitteet = sorted(df_person["Selite"].dropna().astype(str).unique().tolist()) if not df_person.empty else []
            valittu_haku_selite = st.selectbox("Valitse tai kirjoita maksun saaja / selite:", ["(Valitse tai kirjoita...)"] + kaikki_selitteet)
            hakuteksti_input = st.text_input("Tai hae vapaalla hakusanalla (esim. Columbus, Citymarket):", "")
        
        with col_h2:
            haku_aikavali = st.selectbox("Rajaa ajanjaksoa:", ["Kaikki ajat", "Valitse vuosi", "Valitse kuukausi"])
            
            df_haku_pohja = df_person.copy()
            if haku_aikavali == "Valitse vuosi" and not df_haku_pohja.empty and "Pvm_dt" in df_haku_pohja.columns:
                saatavat_vuodet = sorted(df_haku_pohja["Pvm_dt"].dt.year.dropna().astype(int).unique(), reverse=True)
                valittu_haku_vuosi = st.selectbox("Valitse vuosi:", saatavat_vuodet, key="haku_valittu_vuosi")
                df_haku_pohja = df_haku_pohja[df_haku_pohja["Pvm_dt"].dt.year == valittu_haku_vuosi]
            elif haku_aikavali == "Valitse kuukausi" and not df_haku_pohja.empty and "Pvm_dt" in df_haku_pohja.columns:
                saatavat_kk = sorted(df_haku_pohja["Pvm_dt"].dt.strftime("%Y-%m").unique(), reverse=True)
                valittu_haku_kk = st.selectbox("Valitse kuukausi (VVVV-KK):", saatavat_kk)
                df_haku_pohja = df_haku_pohja[df_haku_pohja["Pvm_dt"].dt.strftime("%Y-%m") == valittu_haku_kk]

        df_hakutulos = pd.DataFrame()
        if not df_haku_pohja.empty:
            if valittu_haku_selite and valittu_haku_selite != "(Valitse tai kirjoita...)":
                df_hakutulos = df_haku_pohja[df_haku_pohja["Selite"] == valittu_haku_selite].copy()
            elif hakuteksti_input.strip():
                hakuteksti_lower = hakuteksti_input.strip().lower()
                df_hakutulos = df_haku_pohja[df_haku_pohja["Selite"].str.lower().str.contains(hakuteksti_lower, na=False)].copy()
            else:
                df_hakutulos = df_haku_pohja.copy()
            
        if not df_hakutulos.empty:
            haku_summa_yht = df_hakutulos["Summa"].sum()
            st.success(f"Löytyi {len(df_hakutulos)} kpl tapahtumia. Yhteissumma: **{haku_summa_yht:.2f} €**")
            st.dataframe(df_hakutulos[["Päivämäärä", "Omistaja", "Selite", "Summa", "Kategoria", "kommentti"]], use_container_width=True, hide_index=True)
        elif (valittu_haku_selite and valittu_haku_selite != "(Valitse tai kirjoita...)") or hakuteksti_input.strip() or haku_aikavali != "Kaikki ajat":
            st.info("Ei löytynyt tapahtumia kyseisellä haulla ja ajanjaksolla.")

    st.markdown("---")
    if not df_filtr.empty and "Kategoria" in df_filtr.columns:
        ala_sijoitus_menot = st.checkbox("📌 Älä huomio sijoituksia menoissa", value=False, key="checkbox_ala_sijoitus_menoihin")

        yhteensa_tulot = df_filtr[df_filtr["Summa"] > 0]["Summa"].sum()
        
        df_laskettavat_menot = df_filtr[df_filtr["Summa"] < 0]
        if ala_sijoitus_menot:
            def onko_sijoitus(kat):
                if kat == "Sijoitus":
                    return True
                return ISANTA_MAP.get(kat) == "Sijoitus"
            df_laskettavat_menot = df_laskettavat_menot[~df_laskettavat_menot["Kategoria"].apply(onko_sijoitus)]

        yhteensa_menot = abs(df_laskettavat_menot["Summa"].sum())
        erotus_tulos = yhteensa_tulot - yhteensa_menot

        col_m1, col_m2, col_m3 = st.columns(3)
        col_m1.metric("💰 Tulot", f"{yhteensa_tulot:.2f} €")
        col_m2.metric("💸 Menot", f"{yhteensa_menot:.2f} €", help="Sijoitukset rajattu pois kokonaismenoista" if ala_sijoitus_menot else "")
        
        erotus_vari = "normal" if erotus_tulos >= 0 else "inverse"
        col_m3.metric("⚖️ Erotus", f"{erotus_tulos:.2f} €", delta=f"{erotus_tulos:.2f} €", delta_color=erotus_vari)
        
        st.markdown("---")
        st.subheader("📋 Budjetin toteutuma pääkategorioittain")
        st.caption(f"💡 Budjetit on skaalattu valitulle aikavälille (kerroin kuukausibudjettiin: {kuukausi_kerroin:.2f}x). Pääkategorioiden arvot muodostuvat suoraan niiden alakategorioiden summista.")

        kat_kaytetty_dict = df_filtr.groupby("Kategoria")["Summa"].apply(lambda s: s[s < 0].sum()).abs().to_dict()

        kokonais_budjetti_yhteensa = 0.0
        kokonais_toteutunut_yhteensa = 0.0

        for paakat in PAAKATEGORIAT:
            if paakat == "Tulo":
                continue
            if paakat == "Sijoitus":
                kokonais_budjetti_yhteensa += aktiivinen_budjetti.get("Sijoitus", 0.0)
                kokonais_toteutunut_yhteensa += kat_kaytetty_dict.get("Sijoitus", 0.0)
                continue

            ala_kategoriat = [k for k, isanta in ISANTA_MAP.items() if isanta == paakat]
            pk_kaytetty = kat_kaytetty_dict.get(paakat, 0.0)
            for alakat in ala_kategoriat:
                pk_kaytetty += kat_kaytetty_dict.get(alakat, 0.0)
                kokonais_budjetti_yhteensa += aktiivinen_budjetti.get(alakat, 0.0)
            kokonais_toteutunut_yhteensa += pk_kaytetty

        kokonais_erotus = kokonais_budjetti_yhteensa - kokonais_toteutunut_yhteensa
        kokonais_vari_html = "green" if kokonais_erotus >= 0 else "red"

        st.markdown(
            f"""
            <div style="background-color: #f1f3f4; padding: 20px; border-radius: 10px; margin-bottom: 25px; border-left: 6px solid #3498db;">
                <h3 style="margin: 0; color: #2c3e50; font-size: 1.2rem;">📊 KOKONAISBUDJETIN YHTEENVETO</h3>
                <div style="display: flex; justify-content: space-between; margin-top: 12px; font-size: 1.3rem;">
                    <div>Budjetti yhteensä: <b>{kokonais_budjetti_yhteensa:.2f} €</b></div>
                    <div>Toteutunut: <b>{kokonais_toteutunut_yhteensa:.2f} €</b></div>
                    <div>Erotus: <b style="color: {kokonais_vari_html};">{kokonais_erotus:+.2f} €</b></div>
                </div>
            </div>
            """,
            unsafe_allow_html=True
        )

        for paakat in PAAKATEGORIAT:
            if paakat == "Tulo":
                continue
            
            if paakat == "Sijoitus":
                paakat_kaytetty = kat_kaytetty_dict.get("Sijoitus", 0.0)
                paakat_raja = aktiivinen_budjetti.get("Sijoitus", 0.0)
                paakat_erotus = paakat_raja - paakat_kaytetty
                
                paakat_emoji = "🟢" if paakat_erotus >= 0 else "🔴"
                paakat_vari = "green" if paakat_erotus >= 0 else "red"
                
                otsikko_str = f"{paakat_emoji} **{paakat}** | Käytetty: **{paakat_kaytetty:.2f} €** / Budjetti: **{paakat_raja:.2f} €**"
                with st.expander(otsikko_str):
                    st.markdown(f"Jäljellä: <span style='color: {paakat_vari}; font-weight: bold;'>{paakat_erotus:+.2f} €</span>", unsafe_allow_html=True)
                    st.caption("Sijoitus on suora pääkategorian budjetti.")
                continue

            ala_kategoriat = [k for k, isanta in ISANTA_MAP.items() if isanta == paakat]
            
            paakat_kaytetty = kat_kaytetty_dict.get(paakat, 0.0)
            for alakat in ala_kategoriat:
                paakat_kaytetty += kat_kaytetty_dict.get(alakat, 0.0)

            paakat_raja = 0.0
            for alakat in ala_kategoriat:
                paakat_raja += aktiivinen_budjetti.get(alakat, 0.0)
                
            paakat_erotus = paakat_raja - paakat_kaytetty
            
            paakat_emoji = "🟢" if paakat_erotus >= 0 else "🔴"
            paakat_vari = "green" if paakat_erotus >= 0 else "red"
            
            otsikko_str = f"{paakat_emoji} **{paakat}** | Käytetty: **{paakat_kaytetty:.2f} €** / Budjetti: **{paakat_raja:.2f} €**"
            
            with st.expander(otsikko_str):
                st.markdown(f"Jäljellä: <span style='color: {paakat_vari}; font-weight: bold;'>{paakat_erotus:+.2f} €</span>", unsafe_allow_html=True)
                
                rivi_data = []
                for alakat in ala_kategoriat:
                    a_kaytetty = kat_kaytetty_dict.get(alakat, 0.0)
                    a_raja = aktiivinen_budjetti.get(alakat, 0.0)
                    a_erotus = a_raja - a_kaytetty
                    rivi_data.append({
                        "Kategoria / Alakategoria": f"↳ {alakat}",
                        "Käytetty": a_kaytetty,
                        "Budjetti": a_raja,
                        "Jäljellä / Yli": a_erotus,
                        "Tila": "✅ Ok" if a_erotus >= 0 else "⚠️ Ylitetty"
                    })
                
                if rivi_data:
                    df_alakat_naytto = pd.DataFrame(rivi_data)
                    
                    def muotoile_jalki(val):
                        if pd.isna(val):
                            return ""
                        vari = "green" if val >= 0 else "red"
                        return f"<span style='color: {vari}; font-weight: bold;'>{val:+.2f} €</span>"

                    st.markdown(
                        df_alakat_naytto.assign(
                            **{"Käytetty": df_alakat_naytto["Käytetty"].map(lambda x: f"{x:.2f} €"),
                               "Budjetti": df_alakat_naytto["Budjetti"].map(lambda x: f"{x:.2f} €"),
                               "Jäljellä / Yli": df_alakat_naytto["Jäljellä / Yli"].map(muotoile_jalki)}
                        ).to_html(escape=False, index=False),
                        unsafe_allow_html=True
                    )
                else:
                    st.caption("Ei alakategorioiden tapahtumia tällä aikavälillä.")
    else:
        st.info("Ei tapahtumia valitulla aikavälillä.")

    st.markdown("---")
    st.subheader("📝 Tapahtumat (Muokkaa suoraan taulukossa)")
    
    if not df_filtr.empty:
        df_editor = df_filtr[["id", "Päivämäärä", "Omistaja", "Selite", "Summa", "Kategoria", "kommentti"]].copy()
        df_editor["Päivämäärä"] = pd.to_datetime(df_editor["Päivämäärä"], format="%d-%m-%Y", errors="coerce")
        df_editor.insert(0, "Poista", False)
        
        taulukon_korkeus = min(max(len(df_editor) * 35 + 38, 200), 600)

        edited_df = st.data_editor(
            df_editor,
            column_config={
                "Poista": st.column_config.CheckboxColumn("Poista?", default=False),
                "id": None,
                "Omistaja": st.column_config.SelectboxColumn("Omistaja", options=["Juho", "Veera", "Molemmat (Juho & Veera)"], required=True),
                "Kategoria": st.column_config.SelectboxColumn("Kategoria", options=KATEGORIAT_LISTA, required=True),
                "Summa": st.column_config.NumberColumn("Summa (€)", format="%.2f €"),
                "Päivämäärä": st.column_config.DateColumn("Päivämäärä", format="DD-MM-YYYY"),
                "Selite": st.column_config.TextColumn("Selite"),
                "kommentti": st.column_config.TextColumn("💬 Kommentti"),
            },
            disabled=False,
            num_rows="dynamic",
            use_container_width=True,
            height=taulukon_korkeus,
            key="editor",
        )
        
        col_btn1, col_btn2 = st.columns(2)
        if col_btn1.button("💾 Tallenna muutokset"):
            paivitetut = edited_df[edited_df["Poista"] == False].drop(columns=["Poista"])
            paivita_tapahtumat_db(paivitetut)
            st.session_state["paivita_db"] = True
            st.success("Muutokset tallennettu onnistuneesti!")
            st.rerun()
            
        poistettavat_id = edited_df[(edited_df["Poista"] == True) & (edited_df["id"].notna())]["id"].tolist()
        if poistettavat_id and col_btn2.button(f"🗑️ Poista valitut ({len(poistettavat_id)} kpl)", type="primary"):
            poista_yksittaiset_tapahtumat(poistettavat_id)
            st.session_state["paivita_db"] = True
            st.success("Valitut tapahtumat poistettu!")
            st.rerun()

with tab2:
    st.subheader("🗓️ Vuosikatsaus")
    
    loydetyt_vuodet = []
    if not df_person.empty and "Pvm_dt" in df_person.columns:
        loydetyt_vuodet = sorted(list(df_person["Pvm_dt"].dt.year.dropna().astype(int).unique()), reverse=True)
    if not loydetyt_vuodet:
        loydetyt_vuodet = [datetime.now().year]

    valittu_vuosi_paa = st.selectbox("Valitse tarkasteluvuosi:", loydetyt_vuodet, index=0)
    ota_vertailu = st.checkbox("🔄 Ota käyttöön kahden vuoden vertailu")
    
    vuosi_b = None
    if ota_vertailu:
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            st.info(f"Päävuosi: **{valittu_vuosi_paa}**")
        with col_v2:
            verrokki_list = [v for v in loydetyt_vuodet if v != valittu_vuosi_paa]
            if not verrokki_list:
                verrokki_list = loydetyt_vuodet
            vuosi_b = st.selectbox("Valitse verrokkivuosi:", verrokki_list)

    kk_nimet = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]
    kk_otsikot = ["Tammi", "Helmi", "Maalis", "Huhti", "Toukokuu", "Kesä", "Heinä", "Elo", "Syys", "Loka", "Marras", "Joulu"]

    df_menot = df_person[df_person["Summa"] < 0].copy()
    if not df_menot.empty:
        df_menot["Vuosi"] = df_menot["Pvm_dt"].dt.year
        df_menot["KK"] = df_menot["Pvm_dt"].dt.strftime("%m")

        df_a = df_menot[df_menot["Vuosi"] == valittu_vuosi_paa]
        pivot_a = df_a.groupby(["Kategoria", "KK"])["Summa"].apply(lambda s: abs(s.sum())).unstack(fill_value=0.0)
        
        pivot_b = None
        if ota_vertailu and vuosi_b is not None:
            df_b = df_menot[df_menot["Vuosi"] == vuosi_b]
            pivot_b = df_b.groupby(["Kategoria", "KK"])["Summa"].apply(lambda s: abs(s.sum())).unstack(fill_value=0.0)

        st.markdown("---")

        def hae_kk_summat_suora(pivot_df, kat_nimi):
            if pivot_df is None:
                return {kk: 0.0 for kk in kk_nimet}
            summat = {kk: 0.0 for kk in kk_nimet}
            if kat_nimi in pivot_df.index:
                for kk in kk_nimet:
                    summat[kk] += pivot_df.loc[kat_nimi, kk] if kk in pivot_df.columns else 0.0
            return summat

        def hae_kk_summat(pivot_df, kat_nimi, alat):
            if pivot_df is None:
                return {kk: 0.0 for kk in kk_nimet}
            summat = {kk: 0.0 for kk in kk_nimet}
            if kat_nimi in pivot_df.index:
                for kk in kk_nimet:
                    summat[kk] += pivot_df.loc[kat_nimi, kk] if kk in pivot_df.columns else 0.0
            for alakat in alat:
                if alakat in pivot_df.index:
                    for kk in kk_nimet:
                        summat[kk] += pivot_df.loc[alakat, kk] if kk in pivot_df.columns else 0.0
            return summat

        for paakat in PAAKATEGORIAT:
            if paakat == "Tulo":
                continue
            
            if paakat == "Sijoitus":
                paakat_a = hae_kk_summat_suora(pivot_a, "Sijoitus")
                paakat_b = hae_kk_summat_suora(pivot_b, "Sijoitus") if pivot_b is not None else {}
                yhteensa_a = sum(paakat_a.values())
                yhteensa_b = sum(paakat_b.values()) if pivot_b is not None else 0.0

                poyta_p_data = {}
                for i, kk in enumerate(kk_nimet):
                    val_a = paakat_a[kk]
                    if ota_vertailu and pivot_b is not None:
                        val_b = paakat_b[kk]
                        ero = val_a - val_b
                        vari_koodi = "green" if val_a < val_b else ("red" if val_a > val_b else "inherit")
                        poyta_p_data[kk_otsikot[i]] = f"<span style='color:{vari_koodi}; font-weight:bold;'>{val_a:.1f}€</span> / {val_b:.1f}€ ({ero:+.1f}€)"
                    else:
                        poyta_p_data[kk_otsikot[i]] = f"{val_a:.1f} €"

                if ota_vertailu and pivot_b is not None:
                    vuosi_vari = "green" if yhteensa_a < yhteensa_b else ("red" if yhteensa_a > yhteensa_b else "inherit")
                    poyta_p_data["Koko Vuosi"] = f"<span style='color:{vuosi_vari}; font-weight:bold;'>{yhteensa_a:.1f}€</span> / {yhteensa_b:.1f}€"
                else:
                    poyta_p_data["Koko Vuosi"] = f"{yhteensa_a:.1f} €"

                with st.expander(f"📁 **SIJOITUS** | Yhteensä: **{yhteensa_a:.1f} €**"):
                    st.markdown("##### Kuukausisummat:")
                    st.markdown(pd.DataFrame([poyta_p_data]).to_html(escape=False, index=False), unsafe_allow_html=True)
                continue

            ala_kategoriat = [k for k, isanta in ISANTA_MAP.items() if isanta == paakat]

            paakat_a = hae_kk_summat(pivot_a, paakat, ala_kategoriat)
            paakat_b = hae_kk_summat(pivot_b, paakat, ala_kategoriat) if pivot_b is not None else {}

            yhteensa_a = sum(paakat_a.values())
            yhteensa_b = sum(paakat_b.values()) if pivot_b is not None else 0.0

            yhteenveto_str = f"📁 **{paakat.upper()}** | Yhteensä: **{yhteensa_a:.1f} €**"
            
            poyta_p_data = {}
            for i, kk in enumerate(kk_nimet):
                val_a = paakat_a[kk]
                if ota_vertailu and pivot_b is not None:
                    val_b = paakat_b[kk]
                    ero = val_a - val_b
                    vari_koodi = "green" if val_a < val_b else ("red" if val_a > val_b else "inherit")
                    poyta_p_data[kk_otsikot[i]] = f"<span style='color:{vari_koodi}; font-weight:bold;'>{val_a:.1f}€</span> / {val_b:.1f}€ ({ero:+.1f}€)"
                else:
                    poyta_p_data[kk_otsikot[i]] = f"{val_a:.1f} €"

            if ota_vertailu and pivot_b is not None:
                vuosi_vari = "green" if yhteensa_a < yhteensa_b else ("red" if yhteensa_a > yhteensa_b else "inherit")
                poyta_p_data["Koko Vuosi"] = f"<span style='color:{vuosi_vari}; font-weight:bold;'>{yhteensa_a:.1f}€</span> / {yhteensa_b:.1f}€"
            else:
                poyta_p_data["Koko Vuosi"] = f"{yhteensa_a:.1f} €"

            with st.expander(yhteenveto_str):
                st.markdown("##### Pääkategorian kuukausisummat:")
                st.markdown(pd.DataFrame([poyta_p_data]).to_html(escape=False, index=False), unsafe_allow_html=True)

                if ala_kategoriat:
                    st.markdown("##### Alakategoriat:")
                    ala_rivit = []
                    for alakat in ala_kategoriat:
                        alakat_a = hae_kk_summat_suora(pivot_a, alakat)
                        alakat_b = hae_kk_summat_suora(pivot_b, alakat) if pivot_b is not None else {}
                        yhteensa_a_a = sum(alakat_a.values())
                        yhteensa_b_a = sum(alakat_b.values()) if pivot_b is not None else 0.0

                        rivi_a = {"Alakategoria": alakat}
                        for i, kk in enumerate(kk_nimet):
                            val_a = alakat_a[kk]
                            if ota_vertailu and pivot_b is not None:
                                val_b = alakat_b[kk]
                                ero = val_a - val_b
                                var = "green" if val_a < val_b else ("red" if val_a > val_b else "inherit")
                                rivi_a[kk_otsikot[i]] = f"<span style='color:{var}; font-weight:bold;'>{val_a:.1f}€</span> / {val_b:.1f}€"
                            else:
                                rivi_a[kk_otsikot[i]] = f"{val_a:.1f} €"
                        
                        if ota_vertailu and pivot_b is not None:
                            v_col = "green" if yhteensa_a_a < yhteensa_b_a else ("red" if yhteensa_a_a > yhteensa_b_a else "inherit")
                            rivi_a["Koko Vuosi"] = f"<span style='color:{v_col}; font-weight:bold;'>{yhteensa_a_a:.1f}€</span> / {yhteensa_b_a:.1f}€"
                        else:
                            rivi_a["Koko Vuosi"] = f"{yhteensa_a_a:.1f} €"
                        ala_rivit.append(rivi_a)
                    
                    st.markdown(pd.DataFrame(ala_rivit).to_html(escape=False, index=False), unsafe_allow_html=True)
                else:
                    st.caption("Ei alakategorioita.")

        st.markdown("---")
        st.subheader("🔍 Selaa alakategorian tapahtumia vuonna " + str(valittu_vuosi_paa))
        st.caption("Valitse alta haluamasi kategoria tai alakategoria (esim. Polttoaine), niin näet kaikki siihen liittyvät tapahtumat listattuna.")
        
        kaikki_selattavat_kat = [k for k in KATEGORIAT_LISTA if k != "Tulo"]
        valittu_tarkastelu_kat = st.selectbox("Valitse kategoria tarkasteluun:", kaikki_selattavat_kat, key="vuosikatsaus_kat_selain")
        
        df_valitun_kat_tapahtumat = df_person[
            (df_person["Pvm_dt"].dt.year == valittu_vuosi_paa) & 
            (df_person["Kategoria"] == valittu_tarkastelu_kat)
        ].copy()
        
        if not df_valitun_kat_tapahtumat.empty:
            summa_yht = df_valitun_kat_tapahtumat["Summa"].sum()
            st.markdown(f"**Löytyi {len(df_valitun_kat_tapahtumat)} kpl tapahtumia kategoriassa '{valittu_tarkastelu_kat}' (Yhteensä: {summa_yht:.2f} €):**")
            st.dataframe(
                df_valitun_kat_tapahtumat[["Päivämäärä", "Omistaja", "Selite", "Summa", "Kategoria", "kommentti"]],
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info(f"Ei tapahtumia kategoriassa '{valittu_tarkastelu_kat}' vuonna {valittu_vuosi_paa}.")

    else:
        st.info("Ei menotapahtumia vuosikatsausta varten.")

with tab3:
    st.subheader("📈 Kaaviot & Diagrammit")
    
    if not df_person.empty and "Pvm_dt" in df_person.columns:
        st.markdown("### 🥧 Menojen jakautuminen pääkategorioittain")
        
        col_pc1, col_pc2 = st.columns(2)
        with col_pc1:
            pois_sijoitus_piirakka = st.checkbox("Piilota 'Sijoitus'-kategorian menot piirakasta", key="piirakka_pois_sijoitus")
        with col_pc2:
            nayta_saasto_osuus = st.checkbox("Lisää Säästöön jäänyt lohko", key="piirakka_nayta_saasto")

        df_menot_kaavio = df_person.copy()
        if pois_sijoitus_piirakka:
            df_menot_kaavio = df_menot_kaavio[df_menot_kaavio["Kategoria"] != "Sijoitus"]

        df_menot_kaavio["Vuosi"] = df_menot_kaavio["Pvm_dt"].dt.year
        df_menot_kaavio["Kuukausi_str"] = df_menot_kaavio["Pvm_dt"].dt.strftime("%Y-%m")
        
        suodatus_tapa = st.radio("Valitse tarkastelutaso piirakkakaavioon:", ["Tietty kuukausi", "Koko vuosi"], horizontal=True, key="piirakka_radio")
        
        df_valittu = pd.DataFrame()
        aikaleima_teksti = ""

        if suodatus_tapa == "Tietty kuukausi":
            saatavat_kk = sorted(df_menot_kaavio["Kuukausi_str"].dropna().unique(), reverse=True)
            if saatavat_kk:
                valittu_kk_kaavio = st.selectbox("Valitse kuukausi:", saatavat_kk, key="piirakka_kk")
                df_valittu = df_menot_kaavio[df_menot_kaavio["Kuukausi_str"] == valittu_kk_kaavio]
                aikaleima_teksti = f"Kuukausi: {valittu_kk_kaavio}"
        else:
            saatavat_vuodet = sorted(df_menot_kaavio["Vuosi"].dropna().unique(), reverse=True)
            if saatavat_vuodet:
                valittu_vuosi_kaavio = st.selectbox("Valitse vuosi:", saatavat_vuodet, key="piirakka_vuosi")
                df_valittu = df_menot_kaavio[df_menot_kaavio["Vuosi"] == valittu_vuosi_kaavio]
                aikaleima_teksti = f"Vuosi: {valittu_vuosi_kaavio}"
        
        if not df_valittu.empty:
            tulot_valittu = df_valittu[df_valittu["Summa"] > 0]["Summa"].sum()
            df_valittu_menot = df_valittu[df_valittu["Summa"] < 0].copy()

            def hae_paakategoria(kat):
                if kat == "Sijoitus":
                    return "Sijoitus"
                if kat in PAAKATEGORIAT:
                    return kat
                return ISANTA_MAP.get(kat, "Muut")

            df_valittu_menot["Pääkategoria"] = df_valittu_menot["Kategoria"].apply(hae_paakategoria)
            df_paakat_summat = df_valittu_menot.groupby("Pääkategoria")["Summa"].sum().abs().reset_index()
            df_paakat_summat.columns = ["Pääkategoria", "Summa"]
            
            kokonaiskulut = df_paakat_summat["Summa"].sum()
            erotus_tulot_menot = tulot_valittu - kokonaiskulut

            if nayta_saasto_osuus and erotus_tulot_menot > 0:
                saasto_df = pd.DataFrame({"Pääkategoria": ["Säästöön jäänyt (Tulot - Menot)"], "Summa": [erotus_tulot_menot]})
                df_paakat_summat = pd.concat([df_paakat_summat, saasto_df], ignore_index=True)
                kokonaiskulut_piirakalle = kokonaiskulut + erotus_tulot_menot
            else:
                kokonaiskulut_piirakalle = kokonaiskulut

            if kokonaiskulut_piirakalle > 0:
                df_paakat_summat["Prosentti"] = (df_paakat_summat["Summa"] / kokonaiskulut_piirakalle) * 100
                st.markdown(f"**Yhteenveto ajalta – {aikaleima_teksti} (Menot yhteensä: {kokonaiskulut:.2f} €)**")
                
                fig_pie = px.pie(df_paakat_summat, names="Pääkategoria", values="Summa", hole=0.4, color_discrete_sequence=px.colors.qualitative.Set3)
                fig_pie.update_traces(textinfo='percent+label', textfont_size=13)
                fig_pie.update_layout(
                    margin=dict(t=20, b=20, l=20, r=20),
                    legend=dict(orientation="v", yanchor="middle", y=0.5, xanchor="left", x=1.05),
                    height=450
                )
                st.plotly_chart(fig_pie, use_container_width=True)
                
                st.markdown("#### Tarkat summat ja osuudet")
                df_paakat_summat = df_paakat_summat.sort_values(by="Summa", ascending=False).reset_index(drop=True)
                taulukko_data = [{"Pääkategoria": r["Pääkategoria"], "Summa (€)": f"{r['Summa']:.2f} €", "Osuus (%)": f"{r['Prosentti']:.1f} %"} for _, r in df_paakat_summat.iterrows()]
                st.dataframe(pd.DataFrame(taulukko_data), use_container_width=True, hide_index=True)

        st.markdown("---")
        st.markdown("### 📊 Tulot ja Menot vertailu (sekä kumulatiivinen tulos)")
        
        saatavat_vuodet_pylvas = sorted(df_person["Pvm_dt"].dt.year.dropna().astype(int).unique(), reverse=True)
        if not saatavat_vuodet_pylvas:
            saatavat_vuodet_pylvas = [datetime.now().year]

        col_p1, col_p2 = st.columns(2)
        with col_p1:
            valittu_vuosi_vertailu = st.selectbox("Valitse vuosi:", saatavat_vuodet_pylvas, key="pylvas_vuosi_valinta")
        with col_p2:
            pois_sijoitus = st.checkbox("Poista 'Sijoitus'-kategorian menot vertailusta ja kumulatiivisesta tuloksesta", key="pylvas_pois_sijoitus")

        df_pylvas = df_person[df_person["Pvm_dt"].dt.year == valittu_vuosi_vertailu].copy()
        if pois_sijoitus:
            df_pylvas = df_pylvas[df_pylvas["Kategoria"] != "Sijoitus"]

        df_pylvas["KK"] = df_pylvas["Pvm_dt"].dt.strftime("%m")
        df_pylvas["Tyyppi"] = df_pylvas["Summa"].apply(lambda s: "Tulot" if s > 0 else "Menot")
        df_pylvas["AbsSumma"] = df_pylvas["Summa"].abs()
        
        piv_pylvas = df_pylvas.groupby(["KK", "Tyyppi"])["AbsSumma"].sum().unstack(fill_value=0.0).reset_index()
        
        kaikki_kk_df = pd.DataFrame({"KK": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]})
        piv_pylvas = pd.merge(kaikki_kk_df, piv_pylvas, on="KK", how="left").fillna(0.0)

        if "Tulot" not in piv_pylvas.columns:
            piv_pylvas["Tulot"] = 0.0
        if "Menot" not in piv_pylvas.columns:
            piv_pylvas["Menot"] = 0.0
            
        kk_nimet_pitka = ["Tammikuu", "Helmikuu", "Maaliskuu", "Huhtikuu", "Toukokuu", "Kesäkuu", "Heinäkuu", "Elokuu", "Syyskuu", "Lokakuu", "Marraskuu", "Joulukuu"]
        piv_pylvas["Kuukausi_Nimi"] = kk_nimet_pitka

        piv_pylvas["Nettotulos"] = piv_pylvas["Tulot"] - piv_pylvas["Menot"]
        piv_pylvas["Kumulatiivinen_Tulos"] = piv_pylvas["Nettotulos"].cumsum()

        fig_bar = go.Figure()
        fig_bar.add_trace(go.Bar(x=piv_pylvas["Kuukausi_Nimi"], y=piv_pylvas["Tulot"], name="Tulot", marker_color="#2ecc71"))
        fig_bar.add_trace(go.Bar(x=piv_pylvas["Kuukausi_Nimi"], y=piv_pylvas["Menot"], name="Menot", marker_color="#e74c3c"))
        fig_bar.add_trace(go.Scatter(
            x=piv_pylvas["Kuukausi_Nimi"], 
            y=piv_pylvas["Kumulatiivinen_Tulos"], 
            name="Kumulatiivinen tulos", 
            mode="lines+markers", 
            line=dict(color="#3498db", width=3),
            yaxis="y2"
        ))

        fig_bar.update_layout(
            barmode="group",
            xaxis_title="Kuukausi",
            yaxis=dict(title="Euroa (€) (Tulot/Menot)"),
            yaxis2=dict(title="Kumulatiivinen tulos (€)", overlaying="y", side="right", showgrid=False),
            hovermode="x unified",
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig_bar, use_container_width=True)

        st.markdown("---")
        st.markdown("### 📈 Budjetti vs. Toteutunut menojen kehitys (Koko vuosi)")
        
        saatavat_vuodet_viiva = sorted(df_person["Pvm_dt"].dt.year.dropna().astype(int).unique(), reverse=True)
        if not saatavat_vuodet_viiva:
            saatavat_vuodet_viiva = [datetime.now().year]
            
        col_v1, col_v2 = st.columns(2)
        with col_v1:
            valittu_vuosi_viiva = st.selectbox("Valitse vuosi viivadiagrammiin:", saatavat_vuodet_viiva, key="viiva_vuosi")
        with col_v2:
            kategoria_optiot = ["Kaikki menot yhteensä"] + PAAKATEGORIAT
            valittu_kat_viiva = st.selectbox("Valitse kategoria:", kategoria_optiot, key="viiva_kat")

        df_vuosi_viiva = df_person[(df_person["Pvm_dt"].dt.year == valittu_vuosi_viiva) & (df_person["Summa"] < 0)].copy()
        
        if valittu_kat_viiva != "Kaikki menot yhteensä":
            def kuuluu_paakategoriaan(kat):
                if valittu_kat_viiva == "Sijoitus":
                    return kat == "Sijoitus"
                if kat == valittu_kat_viiva:
                    return True
                return ISANTA_MAP.get(kat) == valittu_kat_viiva
            df_vuosi_viiva = df_vuosi_viiva[df_vuosi_viiva["Kategoria"].apply(kuuluu_paakategoriaan)]

        df_vuosi_viiva["KK"] = df_vuosi_viiva["Pvm_dt"].dt.strftime("%m")
        toteutunut_kk = df_vuosi_viiva.groupby("KK")["Summa"].sum().abs().to_dict()

        if nakyma == "Yhdistetty (Juho + Veera)":
            b_j = BUDJETIT.get("Juho", {})
            b_v = BUDJETIT.get("Veera", {})
            yhdistetty_b = {k: b_j.get(k, 0.0) + b_v.get(k, 0.0) for k in set(b_j) | set(b_v)}
        else:
            yhdistetty_b = BUDJETIT.get(nakyma, {})

        if valittu_kat_viiva == "Kaikki menot yhteensä":
            sijoitus_b = yhdistetty_b.get("Sijoitus", 0.0)
            muut_alakategoriat_b = sum([raja for kat, raja in yhdistetty_b.items() if kat != "Tulo" and kat != "Sijoitus" and kat not in PAAKATEGORIAT])
            laskettu_kk_budjetti = sijoitus_b + muut_alakategoriat_b
        elif valittu_kat_viiva == "Sijoitus":
            laskettu_kk_budjetti = yhdistetty_b.get("Sijoitus", 0.0)
        else:
            alakat_listassa = [k for k, isanta in ISANTA_MAP.items() if isanta == valittu_kat_viiva]
            laskettu_kk_budjetti = 0.0
            for alakat in alakat_listassa:
                laskettu_kk_budjetti += yhdistetty_b.get(alakat, 0.0)

        viiva_x = []
        viiva_toteutunut = []
        viiva_budjetti = []

        for i, kk_koodi in enumerate(["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"]):
            viiva_x.append(kk_nimet_pitka[i])
            viiva_toteutunut.append(toteutunut_kk.get(kk_koodi, 0.0))
            viiva_budjetti.append(laskettu_kk_budjetti)

        fig_line = go.Figure()
        fig_line.add_trace(go.Scatter(x=viiva_x, y=viiva_toteutunut, mode='lines+markers', name='Toteutunut meno', line=dict(color='#e74c3c', width=3)))
        fig_line.add_trace(go.Scatter(x=viiva_x, y=viiva_budjetti, mode='lines+markers', name='Kuukausibudjetti', line=dict(color='#3498db', width=3, dash='dash')))
        
        fig_line.update_layout(
            title=f"Kuukausittainen kehitys ({valittu_vuosi_viiva}) - {valittu_kat_viiva}",
            xaxis_title="Kuukausi",
            yaxis_title="Euroa (€)",
            hovermode="x unified"
        )
        st.plotly_chart(fig_line, use_container_width=True)

    else:
        st.info("Ei dataa kaavioiden näyttämiseen.")