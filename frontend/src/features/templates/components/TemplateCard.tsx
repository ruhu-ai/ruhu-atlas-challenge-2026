/**
 * Template Card Component
 *
 * Displays a single template in a card format with key information.
 */

import { Star, GitBranch, Wrench, ChevronRight } from 'lucide-react';
import { Card } from '@/components/atoms/card';
import { Badge } from '@/components/atoms/badge';
import { Button } from '@/components/atoms/button';
import { cn } from '@/lib/utils';
import type { AgentTemplate } from '@/api/services/template.service';

export interface TemplateCardProps {
  template: AgentTemplate;
  onClick?: () => void;
}

export function TemplateCard({ template, onClick }: TemplateCardProps) {
  const getCategoryColor = (category: string) => {
    switch (category) {
      case 'sales':
        return 'bg-purple-100 text-purple-800';
      case 'customer-service':
        return 'bg-blue-100 text-blue-800';
      case 'healthcare':
        return 'bg-green-100 text-green-800';
      case 'e-commerce':
        return 'bg-orange-100 text-orange-800';
      case 'telecom':
        return 'bg-cyan-100 text-cyan-800';
      default:
        return 'bg-gray-100 text-gray-800';
    }
  };

  const getAgentTypeColor = (agentType: string) => {
    switch (agentType) {
      case 'voice':
        return 'bg-rose-100 text-rose-800 border-rose-200';
      case 'chat':
        return 'bg-sky-100 text-sky-800 border-sky-200';
      case 'multimodal':
        return 'bg-violet-100 text-violet-800 border-violet-200';
      default:
        return 'bg-gray-100 text-gray-800 border-gray-200';
    }
  };

  const visibleTools = template.tool_types.slice(0, 2);
  const extraToolCount = template.tool_types.length - visibleTools.length;

  return (
    <Card
      className="relative overflow-hidden hover:shadow-lg transition-shadow cursor-pointer group"
      onClick={onClick}
    >
      <div className="p-6 space-y-4">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <h3 className="text-lg font-semibold text-foreground group-hover:text-primary transition-colors">
              {template.name}
            </h3>
            <Badge variant="outline" className={cn('mt-2', getCategoryColor(template.category))}>
              {template.category.replace(/-/g, ' ')}
            </Badge>
          </div>
          {template.is_featured && (
            <Star className="h-5 w-5 fill-yellow-400 text-yellow-400 flex-shrink-0" />
          )}
        </div>

        {/* Description */}
        <p className="text-sm text-muted-foreground line-clamp-3">{template.description.split('\n')[0]}</p>

        {/* Tags */}
        <div className="flex flex-wrap gap-2">
          {template.tags.slice(0, 3).map((tag) => (
            <Badge key={tag} variant="secondary" className="text-xs">
              {tag}
            </Badge>
          ))}
          {template.tags.length > 3 && (
            <Badge variant="secondary" className="text-xs">
              +{template.tags.length - 3}
            </Badge>
          )}
        </div>

        {/* Stats row */}
        <div className="flex items-center justify-between text-sm text-muted-foreground pt-2 border-t">
          <div className="flex items-center gap-4">
            {/* State count */}
            <div className="flex items-center gap-1">
              <GitBranch className="h-4 w-4" />
              <span>{template.step_count} states</span>
            </div>
            {/* Tool types */}
            {template.tool_types.length > 0 && (
              <div className="flex items-center gap-1">
                <Wrench className="h-4 w-4" />
                <span>
                  {visibleTools.join(', ')}
                  {extraToolCount > 0 && ` +${extraToolCount}`}
                </span>
              </div>
            )}
          </div>
          {/* Agent type badge */}
          <Badge
            variant="outline"
            className={cn('text-xs', getAgentTypeColor(template.default_agent_settings.agent_type))}
          >
            {template.default_agent_settings.agent_type}
          </Badge>
        </div>

        {/* Hover action */}
        <div className="opacity-0 group-hover:opacity-100 transition-opacity">
          <Button variant="ghost" size="sm" className="w-full">
            View Details
            <ChevronRight className="h-4 w-4 ml-1" />
          </Button>
        </div>
      </div>

      {/* Featured ribbon */}
      {template.is_featured && (
        <div className="absolute top-0 right-0 bg-gradient-to-br from-yellow-400 to-yellow-500 text-white text-xs font-semibold px-3 py-1 rounded-bl-lg">
          FEATURED
        </div>
      )}
    </Card>
  );
}
