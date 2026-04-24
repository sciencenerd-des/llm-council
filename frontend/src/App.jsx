import { useState, useEffect, useCallback } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);

  const loadConversations = useCallback(async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  }, []);

  const loadConversation = useCallback(async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  }, []);

  // Load conversations on mount
  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // Load conversation details when selected
  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId, loadConversation]);

  const handleNewConversation = useCallback(async () => {
    try {
      const newConv = await api.createConversation();
      setConversations((prev) => [
        { id: newConv.id, created_at: newConv.created_at, title: newConv.title, message_count: 0 },
        ...prev,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  }, []);

  const handleSelectConversation = useCallback((id) => {
    setCurrentConversationId(id);
  }, []);

  const handleSendMessage = useCallback(async (content, file = null) => {
    if (!currentConversationId) return;

    setIsLoading(true);
    try {
      // Optimistically add user message to UI (with file info if present)
      const userMessage = { role: 'user', content };
      if (file) {
        userMessage.file = { filename: file.name, file_type: 'csv' };
      }
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      // Create a partial assistant message that will be updated progressively
      const assistantMessage = {
        role: 'assistant',
        stage1: [],  // Start as empty array for progressive updates
        stage2: [],  // Start as empty array for progressive updates
        stage3: null,
        metadata: null,
        loading: {
          stage1: false,
          stage2: false,
          stage3: false,
        },
        pendingModels: 0,       // Track Stage 1 pending models
        pendingStage2Models: 0, // Track Stage 2 pending models
      };

      // Add the partial assistant message
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      // Send message with streaming (using CSV endpoint if file present, otherwise regular)
      const sendFn = file
        ? api.sendMessageWithCSVStream.bind(api, currentConversationId, content, file)
        : api.sendMessageStream.bind(api, currentConversationId, content);

      await sendFn((eventType, event) => {
        switch (eventType) {
          case 'stage1_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastIdx = messages.length - 1;
              // Create a new object to avoid mutating previous state
              messages[lastIdx] = {
                ...messages[lastIdx],
                loading: { ...messages[lastIdx].loading, stage1: true },
                stage1: [],
                pendingModels: event.model_count || 4,
              };
              return { ...prev, messages };
            });
            break;

          case 'stage1_model_complete':
            // Progressive update: add each model's result as it completes
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastIdx = messages.length - 1;
              const lastMsg = messages[lastIdx];
              // Create a new object to avoid mutating previous state
              messages[lastIdx] = {
                ...lastMsg,
                stage1: [...(lastMsg.stage1 || []), event.data],
                pendingModels: event.total_count - event.completed_count,
              };
              return { ...prev, messages };
            });
            break;

          case 'stage1_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastIdx = messages.length - 1;
              // Create a new object to avoid mutating previous state
              messages[lastIdx] = {
                ...messages[lastIdx],
                loading: { ...messages[lastIdx].loading, stage1: false },
                stage1: event.data,
                pendingModels: 0,
              };
              return { ...prev, messages };
            });
            break;

          case 'stage2_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastIdx = messages.length - 1;
              // Create a new object to avoid mutating previous state
              messages[lastIdx] = {
                ...messages[lastIdx],
                loading: { ...messages[lastIdx].loading, stage2: true },
                stage2: [],
                pendingStage2Models: event.model_count || 4,
              };
              return { ...prev, messages };
            });
            break;

          case 'stage2_model_complete':
            // Progressive update: add each model's ranking as it completes
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastIdx = messages.length - 1;
              const lastMsg = messages[lastIdx];
              // Create a new object to avoid mutating previous state
              messages[lastIdx] = {
                ...lastMsg,
                stage2: [...(lastMsg.stage2 || []), event.data],
                pendingStage2Models: event.total_count - event.completed_count,
              };
              return { ...prev, messages };
            });
            break;

          case 'stage2_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastIdx = messages.length - 1;
              // Create a new object to avoid mutating previous state
              messages[lastIdx] = {
                ...messages[lastIdx],
                loading: { ...messages[lastIdx].loading, stage2: false },
                stage2: event.data,
                metadata: event.metadata,
                pendingStage2Models: 0,
              };
              return { ...prev, messages };
            });
            break;

          case 'stage3_start':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastIdx = messages.length - 1;
              // Create a new object to avoid mutating previous state
              messages[lastIdx] = {
                ...messages[lastIdx],
                loading: { ...messages[lastIdx].loading, stage3: true },
              };
              return { ...prev, messages };
            });
            break;

          case 'stage3_complete':
            setCurrentConversation((prev) => {
              const messages = [...prev.messages];
              const lastIdx = messages.length - 1;
              // Create a new object to avoid mutating previous state
              messages[lastIdx] = {
                ...messages[lastIdx],
                loading: { ...messages[lastIdx].loading, stage3: false },
                stage3: event.data,
              };
              return { ...prev, messages };
            });
            break;

          case 'title_complete':
            // Reload conversations to get updated title
            loadConversations();
            break;

          case 'complete':
            // Stream complete, reload conversations list
            loadConversations();
            setIsLoading(false);
            break;

          case 'error':
            console.error('Stream error:', event.message);
            setIsLoading(false);
            break;

          default:
            console.log('Unknown event type:', eventType);
        }
      });
    } catch (error) {
      console.error('Failed to send message:', error);
      // Remove optimistic messages on error
      setCurrentConversation((prev) => ({
        ...prev,
        messages: prev.messages.slice(0, -2),
      }));
      setIsLoading(false);
    }
  }, [currentConversationId, loadConversations]);

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={handleSelectConversation}
        onNewConversation={handleNewConversation}
      />
      <ChatInterface
        conversation={currentConversation}
        onSendMessage={handleSendMessage}
        isLoading={isLoading}
      />
    </div>
  );
}

export default App;
