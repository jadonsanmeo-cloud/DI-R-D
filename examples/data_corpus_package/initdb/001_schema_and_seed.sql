CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS vectordb;

CREATE TABLE IF NOT EXISTS customers (
    customer_id TEXT PRIMARY KEY,
    country TEXT NOT NULL,
    segment TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    country TEXT NOT NULL,
    status TEXT NOT NULL,
    revenue NUMERIC(12, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    product_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    price NUMERIC(12, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS support_tickets (
    ticket_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    topic TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS web_events (
    event_id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers(customer_id),
    event_type TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    document_type TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vectordb.document_chunks (
    chunk_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(document_id),
    content TEXT NOT NULL,
    embedding vector(5) NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
    ON vectordb.document_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 1);

INSERT INTO customers (customer_id, country, segment) VALUES
    ('cust_001', 'US', 'enterprise'),
    ('cust_002', 'CA', 'mid_market'),
    ('cust_003', 'US', 'startup')
ON CONFLICT (customer_id) DO NOTHING;

INSERT INTO orders (order_id, customer_id, country, status, revenue) VALUES
    ('ord_1001', 'cust_001', 'US', 'complete', 1200.00),
    ('ord_1002', 'cust_002', 'CA', 'complete', 850.50),
    ('ord_1003', 'cust_003', 'US', 'pending', 430.25)
ON CONFLICT (order_id) DO NOTHING;

INSERT INTO products (product_id, name, category, price) VALUES
    ('prod_001', 'Insight Dashboard', 'analytics', 299.00),
    ('prod_002', 'Embedding Search', 'retrieval', 199.00),
    ('prod_003', 'Workflow Monitor', 'operations', 149.00)
ON CONFLICT (product_id) DO NOTHING;

INSERT INTO support_tickets (ticket_id, customer_id, status, priority, topic) VALUES
    ('ticket_001', 'cust_001', 'closed', 'high', 'invoice export'),
    ('ticket_002', 'cust_002', 'open', 'medium', 'semantic search'),
    ('ticket_003', 'cust_003', 'pending', 'low', 'csv import')
ON CONFLICT (ticket_id) DO NOTHING;

INSERT INTO web_events (event_id, customer_id, event_type, occurred_at) VALUES
    ('evt_001', 'cust_001', 'view_dashboard', '2026-07-01T09:00:00Z'),
    ('evt_002', 'cust_002', 'search_documents', '2026-07-01T09:05:00Z'),
    ('evt_003', 'cust_003', 'upload_csv', '2026-07-01T09:10:00Z')
ON CONFLICT (event_id) DO NOTHING;

INSERT INTO documents (document_id, title, source_uri, document_type) VALUES
    ('doc_orders_summary', 'Orders Summary', 'raw/txt/orders_summary.txt', 'text'),
    ('doc_customer_segments', 'Customer Segments', 'raw/txt/customer_segments.txt', 'text'),
    ('doc_product_catalog', 'Product Catalog', 'raw/txt/product_catalog.txt', 'text'),
    ('doc_support_notes', 'Support Notes', 'raw/txt/support_notes.txt', 'text'),
    ('doc_web_activity', 'Web Activity', 'raw/txt/web_activity.txt', 'text')
ON CONFLICT (document_id) DO NOTHING;

INSERT INTO vectordb.document_chunks (chunk_id, document_id, content, embedding, metadata) VALUES
    (
        'chunk_orders_summary_001',
        'doc_orders_summary',
        'The orders dataset tracks customer purchases by country, status, and revenue. Completed orders from US and CA customers are available for revenue analysis, while pending orders show pipeline activity.',
        '[0.10,0.20,0.30,0.40,0.50]',
        '{"topic":"orders","source_file":"raw/txt/orders_summary.txt","embedding_model":"openai/text-embedding-3-small","mock":true}'::jsonb
    ),
    (
        'chunk_customer_segments_001',
        'doc_customer_segments',
        'Customer segments include enterprise, mid-market, and startup accounts. Segment metadata helps explain different product usage and support needs across the mock corpus.',
        '[0.20,0.10,0.40,0.30,0.60]',
        '{"topic":"customers","source_file":"raw/txt/customer_segments.txt","embedding_model":"openai/text-embedding-3-small","mock":true}'::jsonb
    ),
    (
        'chunk_product_catalog_001',
        'doc_product_catalog',
        'The product catalog includes analytics dashboards, embedding search, and workflow monitoring products. Product categories connect revenue records to analytics, retrieval, and operations workflows.',
        '[0.30,0.50,0.10,0.20,0.40]',
        '{"topic":"products","source_file":"raw/txt/product_catalog.txt","embedding_model":"openai/text-embedding-3-small","mock":true}'::jsonb
    ),
    (
        'chunk_support_notes_001',
        'doc_support_notes',
        'Support tickets capture customer issues such as invoice export, semantic search, and CSV import. Ticket priority and status provide operational context for customer health analysis.',
        '[0.40,0.10,0.20,0.60,0.30]',
        '{"topic":"support","source_file":"raw/txt/support_notes.txt","embedding_model":"openai/text-embedding-3-small","mock":true}'::jsonb
    ),
    (
        'chunk_web_activity_001',
        'doc_web_activity',
        'Web event logs show user activity such as dashboard views, document searches, and CSV uploads. These events help connect product usage behavior to customer and order records.',
        '[0.50,0.30,0.20,0.10,0.40]',
        '{"topic":"web_events","source_file":"raw/txt/web_activity.txt","embedding_model":"openai/text-embedding-3-small","mock":true}'::jsonb
    )
ON CONFLICT (chunk_id) DO NOTHING;
