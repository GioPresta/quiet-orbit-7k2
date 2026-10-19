# Deploy sempre acceso senza Docker

Vercel non è adatto a questo programma: lo scanner deve restare in esecuzione,
mantenere WebSocket e aggiornare file ogni pochi secondi; le funzioni serverless
hanno limiti di durata e non hanno un processo persistente. Per un servizio
gratuito sempre acceso la scelta pratica è una VM **Oracle Cloud Always Free**
(Ubuntu 22.04, Ampere ARM), senza Docker.

## Prima installazione

1. Crea una VM Always Free e apri solo la porta SSH 22 nel security list.
   Non esporre la 8765 a Internet: la dashboard non ha autenticazione remota.
2. Copia il progetto sulla VM:

   ```bash
   git clone <URL_DEL_REPOSITORY> ~/arbitraggio
   cd ~/arbitraggio
   ```

   In alternativa usa `scp`/`rsync` dalla tua macchina.

3. Installa e avvia il servizio:

   ```bash
   bash deploy/install.sh
   ```

4. Se vuoi Opinion o Predict, inserisci le chiavi in `.env`:

   ```bash
   nano ~/arbitraggio/.env
   sudo systemctl restart arbitraggio
   ```

5. Controlla:

   ```bash
   sudo systemctl status arbitraggio
   sudo journalctl -u arbitraggio -f
   ```

## Vedere la dashboard dal tuo computer

La dashboard ascolta solo su loopback della VM. Apri un tunnel SSH dalla tua
macchina:

```bash
ssh -L 8765:127.0.0.1:8765 ubuntu@IP_DELLA_VM
```

Poi apri `http://127.0.0.1:8765` nel browser locale. Il tunnel evita di esporre
dati e CSV senza autenticazione.

## Aggiornamenti e dati

```bash
   cd ~/arbitraggio
git pull
sudo systemctl restart arbitraggio
```

I dati persistenti sono in `output/`: catalogo, latest, storico snapshot,
`opportunities-latest.csv`, `opportunities-legs.csv`,
`opportunities-sizes.csv`, ledger e storico tracking. Fai un backup periodico
di questa directory.

## Alternative gratuite

Render, Railway e simili hanno piani gratuiti che sospendono i servizi o
limitano i processi in background; Vercel è serverless. Per un monitoraggio
continuo con spegnimento del PC serve una VM o un dispositivo sempre acceso.
Oracle Always Free è la soluzione più semplice senza Docker e senza costi.

## Alternativa senza carta: GitHub Actions

Se non riesci a creare un account Oracle, il repository include
`.github/workflows/collect.yml`: ogni 15 minuti GitHub esegue uno scan,
aggiorna i CSV e genera `docs/index.html` con gli ultimi risultati. Non serve
una carta, Docker o un PC acceso; per i repository pubblici i minuti Actions
sono gratuiti. Attiva GitHub Pages scegliendo la cartella `/docs`.

È un campionamento ogni 15 minuti, non un feed continuo: per un esperimento di
una settimana è sufficiente e conserva lo storico in `docs/` e nei CSV. Per il
real-time continuo serve una VM sempre accesa.
