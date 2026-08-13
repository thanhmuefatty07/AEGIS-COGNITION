#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BinarySchemaId(pub u64);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FieldDescriptor {
    pub name: &'static str,
    pub offset: usize,
    pub size: usize,
}

#[derive(Debug, Clone)]
pub struct SchemaRegistry {
    pub schema_id: BinarySchemaId,
    pub version: u32,
    pub alignment: usize,
    pub fields: &'static [FieldDescriptor],
}

impl SchemaRegistry {
    pub fn validate(
        &self,
        received_schema_id: BinarySchemaId,
        received_version: u32,
    ) -> Result<(), &'static str> {
        if self.schema_id != received_schema_id {
            return Err("schema id mismatch");
        }
        if self.version != received_version {
            return Err("schema version mismatch");
        }
        if !self.has_required_alignment() {
            return Err("schema alignment mismatch");
        }
        Ok(())
    }

    pub fn has_required_alignment(&self) -> bool {
        self.alignment == 64 && self.alignment.is_power_of_two()
    }

    pub fn fields_are_monotonic(&self) -> bool {
        self.fields
            .windows(2)
            .all(|pair| pair[0].offset + pair[0].size <= pair[1].offset)
    }

    pub fn is_valid(&self) -> bool {
        self.has_required_alignment() && self.fields_are_monotonic()
    }
}

pub const NERVE_FIELDS: &[FieldDescriptor] = &[
    FieldDescriptor {
        name: "message_id",
        offset: 0,
        size: 16,
    },
    FieldDescriptor {
        name: "session_id",
        offset: 16,
        size: 16,
    },
    FieldDescriptor {
        name: "sender",
        offset: 32,
        size: 16,
    },
    FieldDescriptor {
        name: "kind",
        offset: 48,
        size: 4,
    },
    FieldDescriptor {
        name: "timestamp",
        offset: 56,
        size: 8,
    },
];

pub const NERVE_SCHEMA: SchemaRegistry = SchemaRegistry {
    schema_id: BinarySchemaId(0xAE1515),
    version: 1,
    alignment: 64,
    fields: NERVE_FIELDS,
};
