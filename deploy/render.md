# Deploy Render senza Docker

Render può eseguire direttamente il comando Python del progetto.

1. Crea un account Render e scegli **New → Blueprint**.
2. Collega il repository `GioPresta/quiet-orbit-7k2`.
3. Render rileva `render.yaml` e crea il servizio `arbitraggio`.
4. Inserisci `PREDICT_API_KEY`, `OPINION_API_KEY` e `LIMITLESS_API_KEY` nelle Environment Variables.
5. Attendi il build; il servizio espone la dashboard sulla porta assegnata da Render.

Il servizio avvia dashboard, scanner e tracker nello stesso processo supervisor
(`run_all.py`). Il piano free limita RAM/CPU e può sospendere il servizio dopo
inattività. Per mantenerlo attivo servono richieste periodiche o un piano
sempre attivo.

Per il piano Free usa un monitor HTTP gratuito verso
`https://IL_TUO_SERVIZIO.onrender.com/health` ogni 5 minuti. Il monitor evita
lo spin-down dopo 15 minuti; Render concede 750 ore di istanza al mese, quindi
un solo servizio 24/7 rientra nel limite (circa 720 ore). Non impostare ping
più frequenti del necessario. Render può comunque sospendere servizi che
generano troppo traffico in uscita.

Il blueprint usa `SCAN_PAGES=5` e `PREDICT_PAGES=50` per stare sotto i 512 MB
del piano Free. È una copertura ridotta rispetto al catalogo completo locale;
per il catalogo completo serve un piano Render con più RAM.

**Persistenza:** su Render free il filesystem è effimero. I CSV e il ledger
vivono nel servizio e possono sparire a un riavvio o redeploy. Per conservare
una settimana di dati senza perdite serve un persistent disk Render (a
pagamento) oppure un database esterno gratuito (Supabase/Postgres) configurato
nel progetto. Il vecchio sito GitHub resta consultabile come snapshot statico,
ma le Actions del profilo sono state disabilitate.

Per il monitoraggio continuo senza sospensioni e senza disco a pagamento, una
VM Always Free è più adatta; Render free è utile per la dashboard e per test,
ma non garantisce un processo né uno storage permanente.
