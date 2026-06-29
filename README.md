# AI-Powered Transaction Processing Pipeline

Django REST Framework API with PostgreSQL, Redis, and Celery for asynchronous
CSV transaction processing. The system accepts a transaction CSV, stores a job,
processes it in the background, applies cleaning/anomaly/LLM enrichment logic,
and exposes polling APIs for status and results.

## Run

Run the full stack from the repository root:

```bash
docker compose up --build
```

The API starts on `http://localhost:8000`.

## Detailed Architecture

```mermaid
flowchart TB
    Client["Client / Postman / curl"] -->|"HTTP requests"| API["Django REST Framework API"]

    subgraph Django["Django application container"]
        API --> Router["URL router<br/>config/urls.py"]
        Router --> Views["jobs views<br/>health, upload, status, results, list"]
        Views --> Serializers["DRF serializers<br/>shape API responses"]
        Views --> Models["Django models<br/>Job, Transaction, JobSummary"]
        Views --> Media["Uploaded CSV storage<br/>MEDIA_ROOT"]
        Views --> CeleryProducer["Celery task producer<br/>process_transaction_job.delay"]
    end

    CeleryProducer -->|"enqueue job_id"| Redis["Redis broker / result backend"]
    Redis -->|"deliver task"| Worker["Celery worker"]

    subgraph Processing["Background transaction pipeline"]
        Worker --> LoadCSV["Read uploaded CSV"]
        LoadCSV --> Clean["Clean rows<br/>dedupe, parse dates, normalize amount/status/currency"]
        Clean --> Detect["Detect anomalies<br/>3x account median, domestic merchant USD rule"]
        Detect --> Classify["Classify missing categories"]
        Classify --> Gemini{"GEMINI_API_KEY set?"}
        Gemini -->|"yes"| GeminiAPI["Gemini 1.5 Flash"]
        Gemini -->|"no"| LocalFallback["Deterministic local fallback"]
        GeminiAPI --> Summary["Build spend summary<br/>top merchants, category totals, risk level"]
        LocalFallback --> Summary
        Summary --> Persist["Persist processed rows and summary"]
    end

    Models --> Database[("PostgreSQL in Docker<br/>SQLite fallback locally")]
    Persist --> Database
    Media --> Worker
    Database --> Views
    Views -->|"JSON responses"| Client
```

### Main Components

- `Client / Postman / curl`: Sends upload, polling, listing, and result requests.
- `Django REST Framework API`: Validates requests and returns JSON responses.
- `Job`: Tracks each CSV processing run and its status.
- `Transaction`: Stores cleaned transaction rows and anomaly fields.
- `JobSummary`: Stores spend totals, category breakdown, narrative, and risk level.
- `Redis`: Queues background Celery work.
- `Celery worker`: Runs the heavy CSV processing outside the request cycle.
- `Gemini 1.5 Flash`: Optional LLM enrichment when `GEMINI_API_KEY` is configured.
- `Local fallback`: Keeps the project runnable without paid LLM setup.
- `PostgreSQL / SQLite`: Stores jobs, transactions, and summaries.

The worker uses Gemini 1.5 Flash when `GEMINI_API_KEY` is set in `code/.env`;
otherwise it uses a deterministic local fallback so the project runs without paid
API setup.

## API Endpoints and Data Flow

### Upload Transaction CSV

```bash
curl -F "file=@DevOps Assignment/transactions.csv" http://localhost:8000/jobs/upload
```

```mermaid
sequenceDiagram
    participant C as Client
    participant A as DRF Upload API
    participant M as Media Storage
    participant DB as Database
    participant R as Redis
    participant W as Celery Worker

    C->>A: POST /jobs/upload<br/>multipart form-data file=transactions.csv
    A->>A: Validate file exists, is .csv, UTF-8, and has required columns
    A->>M: Store raw CSV file
    A->>DB: Create Job(status=pending, filename, raw row count)
    A->>R: Enqueue process_transaction_job(job_id)
    A-->>C: 202 Accepted<br/>{ "job_id": "..." }
    R-->>W: Worker receives job_id asynchronously
    W->>M: Read uploaded CSV
    W->>DB: Save cleaned transactions, anomalies, summary, completed/failed status
```

Expected response:

```json
{
  "job_id": "6c66036f-a6b8-4df4-95e3-2e5f83d4bcd5"
}
```

### Check Job Status

```bash
curl http://localhost:8000/jobs/<job_id>/status
```

```mermaid
sequenceDiagram
    participant C as Client
    participant A as DRF Status API
    participant DB as Database

    C->>A: GET /jobs/{job_id}/status
    A->>DB: Fetch Job and optional JobSummary by job_id
    DB-->>A: Job status, summary if available, error_message
    A-->>C: 200 OK<br/>id, status, summary, error_message
```

The status can move through `pending`, `processing`, `completed`, or `failed`.
When processing is complete, a brief summary is returned with the status.

### Fetch Job Results

```bash
curl http://localhost:8000/jobs/<job_id>/results
```

```mermaid
sequenceDiagram
    participant C as Client
    participant A as DRF Results API
    participant DB as Database

    C->>A: GET /jobs/{job_id}/results
    A->>DB: Fetch Job, JobSummary, and Transactions
    alt job is completed
        DB-->>A: Cleaned rows, anomalies, category spend, LLM summary
        A-->>C: 200 OK<br/>full processed result payload
    else job is not completed
        A-->>C: 409 Conflict<br/>results not ready yet
    end
```

The completed response includes:

- Cleaned transactions
- Flagged anomalies
- Category spend breakdown
- LLM/local fallback summary
- Raw and cleaned row counts

### List All Jobs

```bash
curl http://localhost:8000/jobs
```

```mermaid
sequenceDiagram
    participant C as Client
    participant A as DRF Jobs List API
    participant DB as Database

    C->>A: GET /jobs
    A->>DB: Fetch all Job records
    DB-->>A: Jobs ordered newest first by created_at
    A-->>C: 200 OK<br/>array of job metadata
```

Each job item contains the job id, status, filename, raw row count, and creation
timestamp.

### List Jobs by Status

```bash
curl http://localhost:8000/jobs?status=completed
```

```mermaid
sequenceDiagram
    participant C as Client
    participant A as DRF Jobs List API
    participant DB as Database

    C->>A: GET /jobs?status=completed
    A->>A: Read status query parameter
    A->>DB: Fetch Job records where status=completed
    DB-->>A: Matching jobs
    A-->>C: 200 OK<br/>filtered array of job metadata
```

Use this endpoint to quickly find jobs in a particular state, for example
`pending`, `processing`, `completed`, or `failed`.

## Processing Pipeline

```mermaid
flowchart LR
    CSV["Uploaded CSV"] --> Validate["Validate required columns"]
    Validate --> Job["Create Job"]
    Job --> Queue["Queue Celery task"]
    Queue --> Read["Read rows"]
    Read --> Clean["Clean and normalize"]
    Clean --> Dedupe["Remove exact duplicates"]
    Dedupe --> Anomaly["Flag anomalies"]
    Anomaly --> Category["Classify missing categories"]
    Category --> Summary["Generate summary"]
    Summary --> Save["Save Transactions and JobSummary"]
    Save --> Complete["Mark Job completed"]
```

## Postman Collection

Import `postman/AI-Powered-Transaction-Processing-Pipeline.postman_collection.json`
into Postman to test all API endpoints. The collection uses `baseUrl` set to
`http://localhost:8000` and stores the uploaded `job_id` automatically as
`jobId` for the status and results requests.

See [code/README.md](code/README.md) for additional pipeline notes.
