# Arbitraggio live

Scanner locale per **Polymarket, Opinion, Predict.fun e Limitless**: scarica cataloghi, abbina automaticamente i mercati, segue i book e calcola le posizioni YES/NO entro il budget, considerando fee, profondità e minimi d'ordine. Nessun acquisto automatico.

## Avvio

Python 3.11+. Ambiente `.venv` già preparato nel workspace; per ricrearlo:

```bash
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

```bash
# Continuo, feed WebSocket e matching automatico; budget TOTALE per hedge
.venv/bin/python -m arbitraggio --budget 25

# Solo provider pubblici, catalogo più ampio
.venv/bin/python -m arbitraggio --budget 100 --venues polymarket limitless --pages 20

# Un ciclo REST, senza mantenere connessioni live
.venv/bin/python -m arbitraggio --budget 25 --once --pages 2

# Alternativa polling REST (nessuna dipendenza extra)
python -m arbitraggio --poll --budget 25

# Solo catalogo e abbinamenti
python -m arbitraggio --discover --pages 10

# Esempio con dati sintetici, separato dai dati reali
python -m arbitraggio --demo --budget 25
```

`Ctrl+C` chiude lo scanner e le connessioni. Budget in USD nominali (stablecoin considerate a parità nominale), non euro. Ogni opportunità è indipendente: il capitale e la liquidità non sono riservati tra opportunità concorrenti.

## API key

I file `.env` e `config.json` sono già predisposti e ignorati da Git. Inserire localmente in `.env`:

```dotenv
OPINION_API_KEY=
PREDICT_API_KEY=
LIMITLESS_API_KEY=
```

- Predict.fun mainnet richiede API key: https://developers.predict.fun/
- Opinion richiede API key per l'Open API: https://docs.opinion.trade/developer-guide/opinion-open-api/authentication
- Polymarket e Limitless: lettura pubblica verificata, inclusi feed WebSocket. Chiave Limitless solo per endpoint che la richiedono; feed pubblici senza chiave.

Non inserire seed phrase o private key. Predict.fun è stato verificato live con API key: discovery paginata e snapshot WebSocket ricevuti. Opinion non è ancora verificato live perché manca la chiave. La disponibilità tecnica di API non certifica assenza di KYC o accessibilità operativa: Polymarket dall'Italia è limitato al trading secondo la piattaforma, e compare qui per dati/simulazioni.

## Matching automatico

Non è più necessario compilare una watchlist a mano: il motore normalizza titoli, cerca coppie su piattaforme diverse, confronta soglie numeriche e unità (2B ≠ 2M), polarità (above ≠ below), negazioni e scadenze note. Per ogni coppia calcola YES(A)+NO(B) e YES(B)+NO(A).

Le opportunità automatiche sono marcate **`candidate_unverified_rules`**: titolo e scadenza non provano identità dell'oracolo, della proposizione YES, delle regole di annullamento o del payout. Non vengono presentate come profitti garantiti. Le regole e i link disponibili sono salvati nel catalogo per verifica.

Si può registrare una verifica manuale in `config.json`; in questo caso il risultato ha stato `simulation`:

```json
{
  "groups": [{
    "name": "Evento con regole confrontate",
    "markets": ["opinion:ID_DAL_CATALOGO", "predict:ID_DAL_CATALOGO"],
    "resolution_verified": true,
    "verification_notes": "Riportare confronto di proposizione, oracolo, scadenza e annullamenti"
  }]
}
```

Default: catalogo fino a 10 pagine per piattaforma; massimo 50 elementi/pagina Polymarket e Predict, 25 Limitless e 20 Opinion (più eventuali figli). Non è un catalogo esaustivo. `--pages 1..20` regola la copertura; Predict usa il cursore restituito dall'API. Si monitorano 12 match (`max_matches`), bilanciando le coppie di piattaforme con pari confidenza più 8 mercati per venue per coppie interne YES/NO (`--markets 1..8`). La discovery recupera anche fino a 500 slug e 500 condition ID Polymarket referenziati da altri venue, con richieste batch, per includere coppie assenti dalle prime pagine per volume. Le possibilità fuori da questi limiti non vengono scaricate. `match_threshold` default 0,88; matching fuzzy deterministico, non equivalenza semantica certificata.

`venue_pair_slots` (default 4) riserva alcune coppie a ogni combinazione di piattaforme, così le opportunità Opinion–Predict e Limitless–Predict non vengono nascoste dal volume delle coppie Polymarket–Predict.

Le piattaforme attive si scelgono con le caselle nella sezione “Imposta il tuo hedge” e vengono salvate in `enabled_venues`. Il matching confronta tutte le combinazioni delle piattaforme attive: non esistono coppie fisse. Se una discovery fallisce temporaneamente, lo scanner conserva l’ultimo catalogo valido di quella piattaforma invece di svuotarlo.

Con `require_rule_consistency` attivo (default), dashboard, CSV e tracking ricevono solo opportunità con stato `consistent` (“coerenza testuale”). Le coppie `review` e `conflict` restano fuori dal flusso operativo; la coerenza testuale non è comunque una garanzia di payout.

## Feed e freschezza

- Polymarket: snapshot e delta del market stream, heartbeat, riconnessione e nuova snapshot.
- Predict.fun: `predictOrderbook/{marketId}` con snapshot completi e risposta ai heartbeat del server.
- Limitless: Socket.IO `/markets`, `orderbookUpdate`, sostituzione del book, controllo versioni e unità raw / 1e6.
- Opinion: `market.depth.diff` invalida il mercato e fa rileggere lo snapshot REST: non si applicano delta senza sequenza a una snapshot non sincronizzata.

I cambiamenti attivano il ricalcolo, con debounce di 1s (`--interval`, minimo live 0,25s). Lo stato reale di connessione e il trasporto di ciascuna posizione sono esportati; una connessione aperta non implica automaticamente che sia arrivato un book.

Disconnessione: cache del venue invalidata, retry con backoff fino a 30s e fallback REST esplicito. Snapshot locali rilette dopo 10s senza aggiornamenti; richieste REST limitate globalmente a circa 3/s per piattaforma, quindi il fallback non ha latenza garantita. Cataloghi aggiornati ogni 60s; la discovery REST può temporaneamente ritardare il ricalcolo anche mentre i thread ricevono eventi. Le sottoscrizioni vengono ricreate se cambia l'insieme di mercati monitorati.

Conferme dei book più vecchie di 20s o con scarto temporale >5s sono escluse (`--max-age`, `--max-skew`). Se manca il timestamp sorgente, il risultato lo dichiara: la ricezione recente non dimostra freschezza della sorgente. Nessuna sostituzione silenziosa con dati demo o book vecchi dopo un errore.

## Commissioni e minimi

**Predict.fun:** `base × min(p, 1-p) × quote`, con `feeRateBps` dal catalogo o base documentata 2%. Nessuno sconto implicito: `fees.predict_discount` può essere `"0.10"` solo se attivo per il proprio account (aliquota base allora ×0,9). Minimo ordine **1 USDT di controvalore**.

**Opinion:** `controvalore × topic_rate × p × (1-p) × sconti`, minimo **$0,25 per fill**, minimo ordine **$5 di controvalore**. Il coefficiente di default 0,04 usa il massimo documentato dell'1% a p=0,5, senza sconti; non è un topic_rate verificato per ogni singolo mercato. Il book aggrega gli ordini: il numero di fill non è noto. Il modello applica un minimo per livello di prezzo, marca la fee `estimated_fee=true` e **può sottostimare il totale** se a un livello corrispondono più fill. Le opportunità Opinion sono sempre stime finché manca questa informazione.

**Polymarket:** formula cash `quote × rate × p × (1-p)` da `feeSchedule` con exponent=1. Zero solo con `feesEnabled=false`; metadati sconosciuti escludono il calcolo.

**Limitless CLOB:** limite prudenziale documentato del **3% delle quote acquistate**, trattenuto in quote. Non si assume 0,03% come nell'esempio dell'utente: sono percentuali differenti. Il modello può escludere opportunità che con la curva esatta sarebbero positive. Quantità nette uguali su entrambe le posizioni; le quantità lorde possono differire.

Fee override specifici, solo con dati verificati:

```json
{
  "fees": {
    "predict_discount": "0",
    "opinion:ID_REALE": {
      "model": "opinion",
      "rate": "0.04",
      "minimum": "0.25",
      "source": "Sostituire con fonte e topic_rate verificati",
      "estimated": true
    }
  }
}
```

`rate` Opinion va già moltiplicato per i tre fattori di sconto verificati; il minimo rimane. Modelli disponibili: `cash_flat`, `cash_curve`, `shares`, `predict`, `opinion`. Un override non deve servire a ignorare fee reali.

`fixed_cost_per_hedge` aggiunge gas/bridge/altri costi conosciuti, default 0 (non scoperti automaticamente). `rounding_reserve` accantona $0,02; non replica esattamente gli arrotondamenti dei fill. `balances` limita il capitale per venue, condiviso se entrambe le posizioni sono sulla stessa piattaforma. `net_share_step` default 0,000001: le quote lorde devono ancora essere rivalidate sui vincoli reali prima di inviare ordini.

## Perché l'esempio da $25 può essere scartato

26,03 quote × $0,012 = circa $0,312 di controvalore: inferiore al minimo Predict di 1 USDT. Su Opinion sarebbe anche sotto i $5 minimi e la sola fee minima è $0,25. La tabella e i dettagli dell'esempio indicano inoltre piattaforme diverse: lo scanner usa sempre la stessa piattaforma/book/fee per riepilogo e singola posizione. I prezzi dell'esempio non sono quote live verificate.

## Output

- `output/catalog.json`: catalogo, stato provider, suggerimenti di abbinamento.
- `output/latest.json`: ultimo ciclo, opportunità e match automatici, errori, copertura e stato stream; sostituzione atomica.
- `output/snapshots.jsonl`: storico completo dei cicli.
- `output/opportunities.csv`: una riga per posizione; schema precedente archiviato se cambia l'intestazione.

Mostra quote, prezzo medio e limite, costo, fee cash, quote trattenute, payout nominale, profitto, ROI, residuo e capacità massima conveniente. L'ottimizzatore considera cambi di livello e minimi fee, massimizzando il profitto assoluto entro budget/saldi; può lasciare budget inutilizzato. Volumi storici separati dalla quantità acquistabile nel book. Liquidità non prenotata, acquisti non atomici. `max_profitable_cost` descrive il sizing a profitto massimo sul book scaricato entro i saldi, non il totale volume disponibile.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Coprono fee da tabella Predict, minimi Opinion/Predict, profondità, budget/saldi condivisi, matching automatico e falsi positivi numerici, snapshot/delta/cancellazioni, versioni, invalidazione e fallback REST. Verifica live effettuata sui feed Polymarket, Limitless e Predict.fun; Opinion necessita ancora di credenziali per la verifica live.

Fonti ufficiali:

- https://docs.predict.fun/the-basics/predict-fees-and-limits
- https://docs.opinion.trade/trade-on-opinion.trade/fees
- https://docs.polymarket.com/trading/fees
- https://docs.limitless.exchange/user-guide/fees
- https://docs.polymarket.com/market-data/realtime-data
- https://dev.predict.fun/general-information-1915499m0
- https://docs.opinion.trade/developer-guide/opinion-websocket/quickstart
- https://docs.limitless.exchange/developers/websocket/overview

## Esecuzione in background

Lo scanner avviato in debug scrive `output/scanner.log`; il PID è in `output/scanner.pid`. Budget corrente $25, venue Predict/Polymarket/Limitless, discovery fino a 10 pagine. `output/latest.json` contiene lo stato dell’ultimo ciclo completato. Per arrestarlo: `kill -INT "$(cat output/scanner.pid)"` dalla directory del progetto.

Debug aggiornato: discovery tramite condition ID Predict, selezione bilanciata tra coppie di venue, contatori di copertura corretti e cache del matching invalidata al cambiamento del catalogo. Su 1.651 mercati: primo matching circa 1,7s, riuso circa 1ms. Test automatici: 47 superati.

La freschezza distingue ultima variazione e verifica dello snapshot: uno snapshot completo appena ricevuto può confermare un book invariato; i delta usano il timestamp sorgente. `source_timestamps` conserva le ore originali, `snapshot_checked_at` le conferme; lo stato `recent_snapshot_old_last_change` dichiara questa situazione. Non vengono resi freschi dati vecchi senza una nuova conferma.

Per ricerche mirate usare `discovery_queries` in config (es. `["Pete Hegseth"]`): i risultati Predict vengono aggiunti prima della discovery dei riferimenti Polymarket. `priority_pairs` (es. `[["polymarket:561264","predict:932143"]]`) riserva una posizione nel monitoraggio senza certificare le regole. Le due posizioni prioritarie vengono richieste insieme per ridurre lo scarto temporale. Queste opzioni non rimuovono i limiti generali di paginazione e numero coppie. Test aggiornati: 49 superati.


## Ricerca estesa e selezione economica

La configurazione attuale segue fino a **64 coppie**, dopo screening di un universo fino a **20.000 abbinamenti**. La discovery Predict legge pagine da 100 fino al cursore finale, entro `--predict-pages` (default 200); lo stato `complete` dichiara se il cursore è esaurito. I riferimenti Polymarket vengono recuperati tutti, in batch da 100, senza il precedente taglio a 500. Gli altri provider mantengono i limiti di `--pages`.

Lo screening usa best ask YES/NO pubblicati dai cataloghi, fee, budget, saldi, minimi e quantità indicative disponibili. Serve a scegliere i book da seguire: non è un'opportunità eseguibile e non sostituisce la successiva verifica di profondità e freschezza. Le coppie sono ordinate per profitto indicativo, con 8 posti in esplorazione a rotazione ogni 120s; i riferimenti prioritari rimangono inclusi. Titoli simili con candidati/entità diversi non vengono uniti dal matcher fuzzy.

`coverage` riporta `candidate_pairs`, `screening_positive_pairs`, `screening_unknown_pairs`, `selected_pairs` e i book realmente verificati. Non viene dichiarata copertura completa se la discovery non ha esaurito il cursore, se si raggiunge il limite di candidati o se i book non sono tutti monitorati.

Il refresh della discovery viene eseguito in background ogni 300s. `--reuse-catalog` permette il riavvio rapido dai metadati salvati; prima di mostrare un'opportunità i book vengono sempre verificati live. La ricerca non acquista posizioni e non garantisce che si possano trovare molte opportunità positive in ogni momento.


## Risultato della ricerca estesa e file aggiornato

Inventario iniziale esteso: 24.699 mercati complessivi; 15.265 Predict, con cursore esaurito. Sono stati confrontati oltre 8.000 abbinamenti, selezionando 64 coppie per margine indicativo e rotazione; questi conteggi possono variare al refresh. Il matcher ammette riferimenti espliciti oppure domande normalizzate identiche: non scambia l'ordine delle decisioni Fed e non unisce partite con regole/date differenti.

`output/opportunities-latest.csv` viene sostituito atomicamente a ogni ciclo e contiene una riga per opportunità, senza duplicati di storico: costi YES/NO, quote, profitto stimato, ROI, capacità, stato delle regole e limitazioni. `opportunities.csv` resta lo storico per posizione.

I book ricevuti su uno stream ordinato restano sincronizzati mentre arrivano heartbeat del server entro 30s; i timestamp originali sono conservati. Questo evita di riscaricare via REST centinaia di book invariati ogni 10s. Se il feed perde liveness, la cache viene invalidata o riletta via REST; i delta arrivati in ritardo non ricevono questa conferma. Test aggiornati: 61 superati.


## Dashboard locale e piani di acquisto

Avvio dell’interfaccia (in un processo separato dallo scanner):

```bash
.venv/bin/python -m arbitraggio.dashboard --port 8765
```

Aprire http://127.0.0.1:8765. La dashboard mostra tutte le opportunità del ciclo monitorato, con ricerca, filtri delle regole e download CSV. La tabella riporta anche la scadenza dichiarata da ciascuna piattaforma, separatamente per le due gambe. Il budget, la priorità e il volume minimo si modificano dalla pagina: il motore li applica al ciclo successivo. I dettagli riportano acquisti YES/NO, quantità lorde e nette bilanciate, prezzi medi e limite, commissioni, capitale residuo e link alle piattaforme. Non vengono inviati ordini.

La priorità predefinita usa il **minore dei volumi 24h dei due mercati**, poi la profondità disponibile e il profitto. I conflitti di regole sono mostrati in fondo alla lista; la copia del loro piano è disabilitata. I dati di volume mancanti restano mancanti. Predict richiede chiamate statistiche separate: l’arricchimento copre fino a 260 mercati candidati per refresh, con cache di 300 secondi, non tutto il catalogo. Opinion resta esclusa dai dati live senza credenziali.

Il pannello uscita simula l’acquisto ora e la rivendita immediata delle quote nette sui bid disponibili, con commissioni di vendita. La colonna “Uscita a spread chiuso” mostra il profitto e il rendimento percentuale sul capitale nello scenario di convergenza, dopo le fee di vendita. Non rappresenta una posizione già posseduta e non prevede prezzi futuri. Se manca profondità per una delle due gambe, non espone un profitto di liquidazione completa. Per Limitless si usa prudenzialmente la commissione massima di vendita documentata dell’1,5%; le stime sono indicate nel piano. Volume elevato non garantisce un’uscita anticipata in profitto.

Il confronto deterministico delle regole controlla domande, testi, fonti di risoluzione, soglie numeriche, date dichiarate e clausole speciali. Espone i documenti originali e i controlli mancanti. **Coerenza testuale non significa copertura certificata al 100%**: restano rischio di risoluzione, ordini non atomici, variazioni dei book e precisione/minimi effettivi. Il calcolo assume parità nominale USD/USDC/USDT e include solo i costi configurati. Non è stato aggiunto un modello AI.

Il server ascolta solo su loopback, non espone `.env` o `config.json` e protegge le modifiche con token e controllo dell’origine. I test automatici comprendono piani di uscita, regole, volumi e API della dashboard: 74 test superati.


### Filtro acquisto immediato per intero budget

Lo scanner pubblica solo piani taker che assorbono gli ask disponibili su entrambi i lati per l’intero budget, incluse fee e riserva. Sono ammessi esclusivamente residui del passo quantitativo (ordine di microdollari), non riduzioni automatiche dell’importo. Profondità insufficiente, saldi insufficienti o profitto netto non positivo sull’intero importo escludono il risultato. Il massimo profitto ottenibile su un importo parziale non basta più. Cambiando budget, la tabella nasconde i piani del budget precedente fino al ricalcolo. Il volume storico serve per ordinare; la quantità acquistabile deriva dalla profondità ask effettiva. Nessuna esclusione geografica viene applicata allo screening. Uno snapshot non riserva la liquidità: i due ordini possono comunque trovare prezzi o quantità cambiati al momento dell’invio.


### Fee in quote e uscita a spread chiuso

Polymarket usa ora `shares_curve` sugli acquisti: fee equivalente cash = lordo × rate × p × (1−p), trattenuta in quote dividendo per p. Perciò il netto per quota lorda è `1 − rate × (1−p)`. Ogni livello ha la propria conversione; capacità e budget sono verificati sulle quote nette. Il costo wallet è il controvalore lordo, senza doppio addebito della fee. La dashboard separa fee cash, quote trattenute e valore equivalente già incluso. Le vendite applicano la curva cash. Limitless mantiene fee buy in quote con limite prudenziale documentato, Opinion cash stimato con minimo per fill, Predict curva documentata in USDT.

“Uscita a spread chiuso” è una simulazione **condizionata**, non un profitto già eseguibile o una previsione: ipotizza futuri bid YES + NO = $1 e quantità sufficienti per entrambe le posizioni nette. Mostra tre allocazioni del movimento di prezzo (prima gamba, metà ciascuna, seconda gamba), sottraendo fee di vendita e costi fissi configurati dal payout lordo e confrontando con il costo d’entrata già comprensivo delle fee buy. Il valore in tabella usa la convergenza simmetrica; i dettagli riportano tutti i prezzi ipotizzati. La scomparsa dell’arbitraggio agli ask non implica bid sommati a $1. Nessuna liquidità futura o tempo di uscita viene dichiarato verificato.

Nei dettagli è presente anche un obiettivo al 50% del profitto a risoluzione: l’incasso **dopo** fee di vendita deve raggiungere costo d’entrata + metà profitto. È una soglia da monitorare, non una posizione o un ordine attivo. Il controllo ai bid correnti resta nei dettagli espandibili. Il CSV include scenario e soglia, identificati separatamente dal profitto a risoluzione.


### Tracking dei wallet

Il tracker è un processo separato che legge solo indirizzi pubblici e non riceve private key:

```bash
.venv/bin/python -m arbitraggio.tracking --interval 8
```

Gli indirizzi si aggiungono dalla voce **Portafoglio** della dashboard, scegliendo la piattaforma: Polymarket usa il proxy wallet del profilo, Predict l’indirizzo del conto, Limitless l’indirizzo del profilo, Opinion l’indirizzo del wallet. Lo stesso indirizzo EVM può essere registrato su più piattaforme. Il tracker interroga le posizioni tramite le API pubbliche o la API key già configurata, legge i bid correnti e calcola il ricavo netto vendendo tutte le quote disponibili, con fee e profondità. Limitless funziona in lettura senza chiave; Opinion richiede `OPINION_API_KEY` e senza chiave il wallet viene mostrato con un errore esplicito. Le posizioni risolte non generano richieste ai book: quelle vincenti sono indicate come riscattabili, quelle perdenti a zero.

I dati pubblici di Opinion (catalogo, book e prezzi) non richiedono API key: lo scanner li usa anche senza credenziali. Per le posizioni Opinion serve invece una chiave. Il pulsante **Connetti Opinion con il wallet** firma localmente il messaggio EIP-712 richiesto da Opinion, invia la firma al proxy locale e salva la chiave restituita in `.env`; il wallet deve essere già registrato su opinion.trade. La private key non viene mai richiesta, letta o salvata dal programma. Se la chiave esiste già, il pulsante prova a recuperarla; la rotazione richiede una nuova firma.

`output/tracking.json` contiene l’ultimo stato; `output/tracking-history.jsonl` salva uno snapshot aggregato ogni 60 secondi (`tracking_history_seconds` in `config.json`). La pagina Portafoglio mostra posizioni aperte, riscattabili o tutte, il P&L se vendessi subito e il grafico dello storico. Sono disponibili `output` CSV per il portafoglio corrente e per lo storico dalla dashboard. I profitti riportati dalle piattaforme come realizzati sono separati dal P&L non realizzato calcolato sui bid.

Il P&L “se vendi ora” non è garantito: il book può cambiare, la profondità può coprire solo una parte delle quote e il ricavo non è ancora nel wallet. Nessun ordine viene inviato.

### Opportunità seguite nel tempo

Lo scanner registra in `output/opportunity-ledger.json` le opportunità il cui rendimento d’ingresso, al netto delle sole fee di piattaforma, è almeno `tracking_min_yield_pct` (default 1,5%). Ogni coppia/direzione riceve un episodio con costo d’ingresso, ROI netto fee iniziale, ROI massimo, valore di uscita ai bid e durata.

Il target di uscita è `tracking_target_yield_pct` (default 1,5% netto fee). A ogni ciclo il tracker simula la vendita di tutte le quote ai bid correnti e sottrae le fee di vendita. Se la vendita è completa e il rendimento netto fee raggiunge il target, l’episodio viene chiuso con motivo `target` e durata registrata; non serve attendere la scadenza. La chiusura è una simulazione di prezzo, non un ordine eseguito.

Gli snapshot vengono aggiunti ogni `tracking_snapshot_seconds` (default 30) in `output/opportunity-tracking.jsonl`. La pagina **Seguite** permette di filtrare per stato, piattaforma, durata, ROI minimo d’ingresso e ricerca, oltre a ordinare per ROI o durata; mostra tempo mediano al target, ROI netto medio e un grafico tempo/rendimento. Il CSV `opportunita-seguite.csv` esporta gli stessi campi. Se lo stesso mercato torna conveniente dopo una chiusura, viene registrato un nuovo episodio.

Un episodio già aperto continua a essere aggiornato anche quando l’opportunità scende sotto la soglia d’ingresso: è proprio il caso in cui lo spread si sta chiudendo. `tracking_exclude_names` esclude from tracking per nome (per esempio Trump); `backfill_from_snapshots` recupera dallo storico la prima rilevazione di un’opportunità precedente senza perdere la durata già osservata.

### CSV dettagliati e size ladder

Oltre a `opportunities-latest.csv`, ogni ciclo produce:

- `opportunities-legs.csv`: una riga per gamba, con URL, volume totale/24h del mercato, miglior ask, size al miglior ask, profondità ask/bid in quote e controvalore, fee e fill;
- `opportunities-sizes.csv`: una riga per budget della scala 10/25/50/100/250/500/1000, con fattibilità, costo, payout, profitto e ROI.

Il risultato JSON include inoltre `max_feasible_budget`: il budget più alto della scala che resta eseguibile e profittevole. I volumi specifici della singola risposta non sono esposti da tutte le API: quando mancano restano vuoti, mentre la profondità del book è sempre quella osservata per quel lato. Questi dati servono a distinguere un opportunità solo teorica da una realmente assorbibile.

Per l’esecuzione continua senza PC acceso, vedi [deploy/README.md](deploy/README.md): Vercel non è adatto a un processo persistente con WebSocket; la configurazione pronta usa una VM Oracle Cloud Always Free con systemd, senza Docker.
