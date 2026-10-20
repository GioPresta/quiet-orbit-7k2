# Persistenza Supabase per Render

Supabase mantiene i CSV e il ledger mentre Render può dormire o riavviarsi.

1. Crea un progetto Supabase gratuito.
2. In **Storage** crea un bucket privato chiamato `arbitraggio`.
3. In Render aggiungi le variabili:
   - `SUPABASE_URL` = URL del progetto, per esempio `https://xxxx.supabase.co`
   - `SUPABASE_SERVICE_KEY` = service role key del progetto
   - `SUPABASE_BUCKET` = `arbitraggio`
4. Su Render il servizio esegue `run_all.py`: scarica `arbitraggio-state.tar.gz`
   all’avvio e lo risalva ogni 5 minuti.

Il file remoto è un archivio compresso che sovrascrive lo stato precedente;
non pubblica le API key e non richiede Docker. La service role key va inserita
solo come secret Render, mai nel repository.

Il bucket gratuito ha limiti di spazio e banda; l’archivio contiene CSV e
ledger, non il catalogo completo. Se Render resta fermo per lunghi periodi,
Supabase può sospendere il progetto per inattività.
