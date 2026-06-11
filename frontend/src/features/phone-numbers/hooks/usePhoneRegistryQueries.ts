/**
 * Phone Numbers — server state: registry list, agent list, selected-number
 * detail, and audit trail, plus the initial-selection effect.
 */

import { startTransition, useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { agentDefinitionService } from '@/api/services/agent-definition.service'
import { phoneService } from '@/api/services/phone.service'
import type { PhoneRegistryNumber } from '@/types/phone'

const EMPTY_PHONE_NUMBERS: PhoneRegistryNumber[] = []

type UsePhoneRegistryQueriesArgs = {
  selectedNumberId: string | null
  setSelectedNumberId: (phoneNumberId: string | null) => void
}

export function usePhoneRegistryQueries({
  selectedNumberId,
  setSelectedNumberId,
}: UsePhoneRegistryQueriesArgs) {
  const numbersQuery = useQuery({
    queryKey: ['phone-registry-numbers'],
    queryFn: () => phoneService.listPhoneNumbers(),
  })

  const agentsQuery = useQuery({
    queryKey: ['phone-registry-agents'],
    queryFn: () => agentDefinitionService.listAgents(),
  })

  const allNumbers = numbersQuery.data ?? EMPTY_PHONE_NUMBERS

  // Auto-select first number on initial load
  useEffect(() => {
    if (allNumbers.length === 0) {
      setSelectedNumberId(null)
      return
    }
    const hasSelected = selectedNumberId && allNumbers.some((number) => number.phone_number_id === selectedNumberId)
    if (!hasSelected) {
      startTransition(() => {
        setSelectedNumberId(allNumbers[0].phone_number_id)
      })
    }
  }, [allNumbers, selectedNumberId, setSelectedNumberId])

  const detailQuery = useQuery({
    queryKey: ['phone-registry-detail', selectedNumberId],
    queryFn: () => phoneService.getPhoneNumberDetail(selectedNumberId as string),
    enabled: Boolean(selectedNumberId),
  })

  const auditQuery = useQuery({
    queryKey: ['phone-registry-audit', selectedNumberId],
    queryFn: () =>
      phoneService.listPhoneAuditEvents({
        phone_number_id: selectedNumberId as string,
        limit: 12,
      }),
    enabled: Boolean(selectedNumberId),
  })

  return { numbersQuery, agentsQuery, detailQuery, auditQuery, allNumbers }
}
