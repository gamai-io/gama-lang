# GAMA-L + GAMA-OS — Izvještaj "šta je gdje" (v0.1)

Autor: **GAMA TRADE** (Renato) — urađeno kroz zajedničko radno okruženje (opencode + AI agenat).
Datum: 27.08.2026.

Ovaj dokument je **mapa cijele stvari** — gdje je šta, šta radi, i kako se pokreće.
Cilj: da Adi (ili bilo ko) brzo skonta suštinu bez čitanja stotinu fajlova.

---

## 1. ŠTA JE OVO — jedna rečenica

> Naš **programski jezik u kojem je AI ugrađen u jezik** (ne kao biblioteka — nego kao KIŠOBRAN),
> plus **operativni sistem** koji pretvara stare/postojeće računare u "AI-native" terminale naše mreže.

Dakle dva proizvoda, jedno crno-bijelo:

| Proizvod | Šta je | Gdje |
|---|---|---|
| **GAMA-L** | AI-first dataflow programski jezik (v0.1) | `gama-lang/` |
| **GAMA-OS** | Vlastita OS-proizvodna linija nad Linux jezgrom | `gama-os/` |

---

## 2. GAMA-L — POLIMORFNI PROGRAMSKI JEZIK (`gama-lang/`)

### Paradigma (zašto je drugačiji od svih)
1. **Pipes (dataflow)** — sve je tok: `izvor | filt X | select A,B | broj`. Pišeš **šta**, ne *kako*.
2. **Podaci su prvi građanin** — tabele/mape su native, bez boilerplate-a.
3. **AI je ugrađen** — `ask`, `remember/recall`, `embed`, `notify` su **KLJUČNE RIJEČI**, ne `import`.
4. **HITL (human-in-the-loop) po defaultu** — `wait user "..."` traži glavu u petlji gdje treba.
5. **Meta-nivo (#tune)** — funkcije nose oznake koje naš AI optimizira (na GPU).

### Struktura foldera
```
gama-lang/
├── gamal.py            ← TRANSPAJLER: GAMA-L → Python (srce jezika, ~160 reda)
├── gamal_runtime.py    ← RUNTIME: spaja kod na infrastrukturu (SQL, AI, mail, memorija)
├── test_e2e.gm         ← end-to-end test (SQL + pipe, bez slanja)
├── test_full.gm        ← pun test (memorija + AI)
├── test_noai.gm        ← test bez AI
├── demo.gm             ← živa demonstracija: kupci iz SQL → pipe → AI → mail
└── gamal_mem.json      ← memorija jezika ({ključ: vrijednost})
```

### Fajl po fajl — šta KOD radi
- **`gamal.py`** — parsira GAMA-L i prepisuje u Python. Podržava:
  `:=` (dodela), pipe (`|`), funkcije `napravi ime(...)`, komentare `#`,
  ključne riječi `sql / ask / remember / recall / notify / wait user`.
- **`gamal_runtime.py`** — funkcije koje transpajler poziva:
  - `ask(prompt, model)` → pitaj AI (Oracle semapi → fallback Vast/Ollama GPU)
  - `sql(query, db)` → SQL na VP2026 (prava prodajna baza, 192.168.88.83)
  - `remember/recall` → memorija u `gamal_mem.json`
  - `notify(...)` → SMTP mail (gamatrade.ba:26)
  - `hitl(...)` → `wait user` (pitanje korisniku)
  - pipe-ovi: `filt / select / suma / mapa / limit / sort / broj`

### Sintaksa (brzi pregled)
```
# komentar
t := sql("SELECT konto,naziv FROM kupdob")     # SQL → tabela
iz := t | filt naziv | select konto, naziv      # pipe: filtriraj, odaberi
n  := iz | broj                                 # pipe: broj tabova

misli := ask local "Šta misliš o ovome?"         # AI (GPU/Oracle)
remember "pamet" = "vrijednost"                  # memorija
pokaz := recall "pamet"                          # dohvati memoriju

potvrda := wait user "De potvrdiš?"              # HITL
notify email to: "x@y.ba" subject: "Naslov" "Tekst"   # obavijest
```

### Dokazano radi (v0.1) — testirano živo
- ✅ SQL iz **prave VP2026 baze**: povučeni stvarni kupci (A&F D.O.O. Jajce, ...)
- ✅ pipe `select` + `broj`
- ✅ `remember / recall` memorija (mem fajl pisan)
- ✅ `ask` → AI odgovor sa Vast GPU / Oracle
- ✅ transpajl: `:=`, pipe, funkcije

### Sljedeći koraci (v0.2 — plan)
- [ ] `#tune` meta-nivo — AI optimizira funkcije na GPU
- [ ] `interface` — automatski UI spec iz deklaracije ("aplikacija po narudžbi")
- [ ] ugrađeni `embed` (semantička pretraga bge-m3)
- [ ] uvoz/izvoz modula
- [ ] TTS/STT (`govori`, `slušaj`) — audio interfejs
- [ ] distribucija runtime-a na svim mašinama

> **Ideja:** jedan GAMA-L fajl = kompletna "aplikacija" po narudžbi — baza + AI +
> interfejs + obavještenja, **bez klasičnog dev stack-a**.

### Pokretanje
```bash
cd gama-lang
python gamal.py demo.gm                          # transpajl u Python (pregled)
python -c "from gamal_runtime import run; import builtins; print(run(open('test_e2e.gm',encoding='utf-8').read()))"
```

---

## 3. GAMA-OS — OPERATIVNI SISTEM (`gama-os/`)

### Šta je
Vlastita operativna porodica nad Linux jezgrom. Svaka mašina u našoj mreži
(nema elektronike, stari krševi) postaje **GAMA-OS terminal — sa AI u sebi**,
umjesto da bude e-waste.

### Struktura foldera
```
gama-os/
├── builder/
│   └── gama-os.sh     ← BUILDER skripta: jedna jezgra, sve role (kiosk/terminal/pro/node)
├── launcher/
│   └── index.html     ← GAMA-OS UI (splash + apps: ponuda/baza/AI/posta/kasa/...)
└── docs/
    └── PRODUKT_LINIJA.md  ← proizvodna linija + cijene + Q-LAB test lista
```

### Verzije (izbor naručioca)
| Verzija | Namjena | Min hardware | Cijena |
|---|---|---|---|
| **KIOSK** (Lite) | oživjeti stari krš | Celeron/Pentium, 512MB-1GB | paket |
| **TERMINAL** | tanak klijent / stanica | 1GB+, i686/x64 | paket |
| **PRO** | = kao naš Linux3 | x64, 2-4GB | paket |
| **NODE** | servisni čvor / backend | x64, 8GB+ | pretplata |

### Lajtmotiv svake verzije
- **Jezgra**: Alpine Linux (ultra-lagan, siguran)
- **UI**: web launcher (GAMA-L `interface`) — radi i na mobitelu
- **AI**: GAMA-L runtime (`ask`, `remember`, `sql`, `notify`)
- **Mreža**: Tailscale + gama-bus (nas protokol za agente)
- **Dojave**: `notify` (SMTP + bus)
- **Sigurnost**: zaključan kiosk, remote-admin, self-update

### Diferenciranje od svih
1. **AI je u jeziku** (GAMA-L), ne dodatak — svaka mašina može da pita/govori.
2. **Sve brendirano GAMA** — splash, launcher, UI.
3. **Cloud-most na našu flotu** — stara mašina = kapacitet, ne e-waste.
4. **Testirano na pravim krševima** (Q-LAB lista).

---

## 4. KAKO JE OVO NASTALO (kontekst rada)

Ovo je dio šireg **"GAMA AI"** sistema koji gradimo paralelno:
- AI agenat + bot (WhatsApp) + SQL pristup VP2026 prodajnoj bazi
- Meta-sistem 400+ skillova za agente
- Eksperiment: **vlastiti model "gama-mini"** treniran na Qwen3-8B (QLoRA), serviran na GPU
- GAMA-L i GAMA-OS su **vlastite tehnologije** unutar tog sistema

> Repo ovdje izdvaja **samo** jezik + OS (završeni v0.1 artefakti),
> da budu pregledni i pokazni Adiju.

---

## 5. STATUS ODMAH
- **GAMA-L v0.1**: radi, testirano end-to-end (SQL + AI + memorija + mail).
- **GAMA-OS v0.1**: builder + UI + dokumentacija linije. Q-LAB testiranje sljedeće.
- Sve **lokalno naš vlasnički rad** — nema tuđeg koda u jezgru.

**Želimo javni feedback**: jezik, sintaksa, ideja OS-a, diferencijacija — sve je otvoreno za kritiku.
