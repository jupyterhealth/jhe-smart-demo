# jhe-smart-demo

demo SMART app launch with JupyterHealth Exchange

THis uses docker compose to run:

- postgres database
- JupyterHealth Exchange
- a SMART Launch App

Currently, the configuration is tied to our demo medplum account as the EHR/FHIR.

## Run the demo

Launch:

```
docker compose up
```

seed the JHE database:

```
cat jhe/seed.py | docker exec -i jhe-smart-demo-jhe-1 python3 manage.py shell
```

visit http://localhost:8080 and proceed through selecting practitioner, patient, and click 'launch demo'.

Click "Login with JHE" to get JHE credentials (this will become unnecessary shortly).

Then you should see info from EHR and JHE together.

## TODO

- [ ] implement token exchange instead of repeat JHE OAuth
