
import React from "react";
import { Link } from "react-router-dom";

const Index = () => {
  return (
    <div className="min-h-screen flex flex-col bg-gradient-to-b from-slate-900 to-slate-800">
      {/* Header */}
      <header className="py-6 px-4 sm:px-6 lg:px-8 border-b border-slate-700">
        <div className="max-w-7xl mx-auto flex justify-between items-center">
          <div className="flex items-center">
            <div className="text-3xl font-bold text-white">blips</div>
            <div className="ml-2 bg-blue-500 text-xs text-white px-2 py-1 rounded-full">API</div>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-grow">
        <div className="max-w-7xl mx-auto py-12 px-4 sm:px-6 lg:px-8">
          <div className="bg-slate-800 border border-slate-700 shadow-xl rounded-xl overflow-hidden">
            <div className="p-8">
              <h1 className="text-3xl font-bold text-white mb-6">Blips AI News Backend</h1>
              
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                
                {/* API Status */}
                <div className="col-span-1 bg-slate-900 rounded-lg p-6 border border-slate-700">
                  <h2 className="text-xl font-semibold text-white mb-4">API Status</h2>
                  <div className="flex items-center space-x-2">
                    <div className="h-3 w-3 bg-green-500 rounded-full"></div>
                    <span className="text-green-400 font-medium">Running</span>
                  </div>
                  <div className="mt-4 text-sm text-slate-400">
                    <p>Base URL: <code className="bg-slate-800 px-2 py-1 rounded">http://localhost:8000</code></p>
                    <p className="mt-2">API Docs: <code className="bg-slate-800 px-2 py-1 rounded">http://localhost:8000/docs</code></p>
                  </div>
                </div>
                
                {/* Key Endpoints */}
                <div className="col-span-2 bg-slate-900 rounded-lg p-6 border border-slate-700">
                  <h2 className="text-xl font-semibold text-white mb-4">Key Endpoints</h2>
                  <div className="space-y-3 text-sm">
                    <div className="flex">
                      <span className="bg-blue-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">GET</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/articles/next</span>
                    </div>
                    <div className="flex">
                      <span className="bg-blue-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">GET</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/articles/{"{id}"}</span>
                    </div>
                    <div className="flex">
                      <span className="bg-blue-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">GET</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/articles/cache</span>
                    </div>
                    <div className="flex">
                      <span className="bg-green-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">POST</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/ai/respond</span>
                    </div>
                    <div className="flex">
                      <span className="bg-blue-600 text-white px-2 py-1 rounded-l w-16 flex items-center justify-center">GET</span>
                      <span className="bg-slate-800 px-3 py-1 rounded-r flex-grow">/api/v1/usage</span>
                    </div>
                  </div>
                </div>
                
                {/* Features */}
                <div className="col-span-1 lg:col-span-3 grid grid-cols-1 md:grid-cols-3 gap-4 mt-2">
                  <div className="bg-slate-900 rounded-lg p-6 border border-slate-700">
                    <h3 className="text-lg font-medium text-white mb-2">News Pipeline</h3>
                    <p className="text-slate-400 text-sm">Automatically fetches, summarizes, and tags tech news articles using OpenAI GPT.</p>
                  </div>
                  <div className="bg-slate-900 rounded-lg p-6 border border-slate-700">
                    <h3 className="text-lg font-medium text-white mb-2">AI Chat</h3>
                    <p className="text-slate-400 text-sm">Chat with AI about any article with full context awareness and conversation history.</p>
                  </div>
                  <div className="bg-slate-900 rounded-lg p-6 border border-slate-700">
                    <h3 className="text-lg font-medium text-white mb-2">Quota Management</h3>
                    <p className="text-slate-400 text-sm">Tracks and limits user interactions by IP/device with configurable quotas.</p>
                  </div>
                </div>
              </div>
              
              <div className="mt-8 border-t border-slate-700 pt-6">
                <p className="text-slate-400 text-sm mb-4">Follow the setup instructions in the README to start using the backend:</p>
                <ol className="list-decimal list-inside text-slate-400 text-sm space-y-2 pl-4">
                  <li>Clone the repository</li>
                  <li>Create a <code className="bg-slate-800 px-1 rounded">.env</code> file from <code className="bg-slate-800 px-1 rounded">.env.example</code></li>
                  <li>Add your OpenAI API key</li>
                  <li>Run <code className="bg-slate-800 px-1 rounded">docker-compose up -d</code></li>
                </ol>
              </div>
            </div>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="bg-slate-900 border-t border-slate-800">
        <div className="max-w-7xl mx-auto py-6 px-4 text-center text-slate-500 text-sm">
          <p>© 2025 Blips AI News | FastAPI + PostgreSQL + Redis + OpenAI</p>
        </div>
      </footer>
    </div>
  );
};

export default Index;
