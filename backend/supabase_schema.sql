-- Supabase Schema for LLM Council
-- Run this SQL in your Supabase SQL Editor (https://app.supabase.com)

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Conversations table
CREATE TABLE IF NOT EXISTS conversations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    title TEXT NOT NULL DEFAULT 'New Conversation'
);

-- Messages table
CREATE TABLE IF NOT EXISTS messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT,
    file_info JSONB,
    stage1 JSONB,
    stage2 JSONB,
    stage3 JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index for faster message lookups by conversation
CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);

-- Index for ordering messages by creation time
CREATE INDEX IF NOT EXISTS idx_messages_created_at ON messages(created_at);

-- Enable Row Level Security (RLS) - optional but recommended
-- Uncomment these if you want to enable RLS

-- ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE messages ENABLE ROW LEVEL SECURITY;

-- Policy to allow all operations (for development)
-- In production, you'd want more restrictive policies

-- CREATE POLICY "Allow all access to conversations" ON conversations FOR ALL USING (true);
-- CREATE POLICY "Allow all access to messages" ON messages FOR ALL USING (true);

-- Grant permissions (if using service role key, this is not needed)
-- GRANT ALL ON conversations TO anon, authenticated;
-- GRANT ALL ON messages TO anon, authenticated;
