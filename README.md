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

visit https://app.medplum.com/Patient/01961612-dbdc-759b-b885-f55117556bb6/apps and launch "local test"

should go through the process of logging in with Google,
authorizing the app, and completing SMART Launch.

Click "Login with JHE" to get JHE credentials

Then you should see info from EHR and JHE

## TODO

- [ ] fetch data and compute a chart
- [ ] deploy a sample EHR instead of medplum so it's self-contained
- [ ] implement token exchange instead of repeat JHE OAuth
