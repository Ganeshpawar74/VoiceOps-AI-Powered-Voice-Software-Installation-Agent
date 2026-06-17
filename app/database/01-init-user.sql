-- Create voiceops role and database
CREATE USER voiceops WITH PASSWORD 'secret';
CREATE DATABASE voiceops OWNER voiceops;

-- Grant privileges on database
GRANT ALL PRIVILEGES ON DATABASE voiceops TO voiceops;
ALTER USER voiceops CREATEDB;

-- Connect to voiceops database to grant table permissions
\c voiceops

-- Grant all permissions on existing tables (will be populated by 02-schema.sql)
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO voiceops;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO voiceops;

-- Set default privileges for future tables created by postgres
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO voiceops;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO voiceops;
